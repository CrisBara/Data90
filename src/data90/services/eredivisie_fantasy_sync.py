"""Sync the current Eredivisie fantasy roster and player statistics."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    Competition,
    DataSource,
    PlayerTeamSeason,
    SyncRun,
    SyncRunItem,
)
from data90.domain.dto import NormalizedPlayerSeasonStatistic, NormalizedSeason
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderError
from data90.providers.eredivisie_fantasy import EredivisieFantasyProvider
from data90.repositories.catalog import upsert_competition_season
from data90.repositories.players import upsert_player_statistic
from data90.services.promiedos_sync import _team_index, _team_source_mappings

SessionFactory = Callable[[], Session]


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sync_payload(
    session: Session,
    payload: dict[str, Any],
    *,
    source_id: int,
    now: datetime,
) -> tuple[int, int, int]:
    teams = [team for team in payload["teams"] if isinstance(team, dict)]
    elements = [player for player in payload["elements"] if isinstance(player, dict)]
    events = [event for event in payload["events"] if isinstance(event, dict)]
    current_event = next(
        (event for event in events if event.get("deadline_time")),
        None,
    )
    date_value = current_event.get("deadline_time") if current_event else None
    season_year = (
        int(str(date_value)[:4])
        if date_value and str(date_value)[:4].isdigit()
        else now.year
        if now.month >= 7
        else now.year - 1
    )
    season = upsert_competition_season(
        session,
        NormalizedSeason(
            source_code=EredivisieFantasyProvider.source_code,
            competition_code="EREDIVISIE",
            external_id=str(season_year),
            external_code="EREDIVISIE",
            external_season=str(season_year),
            start_year=season_year,
            end_year=season_year + 1,
        ),
    )
    team_rows = []
    for item in teams:
        team_rows.append(
            {
                "entity": {
                    "object": {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "short_name": item.get("short_name"),
                    }
                }
            }
        )
    team_ids = _team_source_mappings(session, source_id, season.id, _team_index(session), team_rows)
    endpoint_to_local = {
        str(item["id"]): team_ids[str(item["id"])]
        for item in teams
        if item.get("id") is not None and str(item["id"]) in team_ids
    }
    position_names = {
        item.get("id"): item.get("singular_name")
        for item in payload.get("element_types", [])
        if isinstance(item, dict)
    }
    inserted = updated = skipped = 0
    for item in elements:
        player_id = item.get("code") or item.get("id")
        full_name = " ".join(
            part
            for part in (item.get("first_name"), item.get("second_name"))
            if isinstance(part, str) and part.strip()
        )
        display_name = full_name or item.get("web_name")
        if player_id is None or not isinstance(display_name, str) or not display_name.strip():
            skipped += 1
            continue
        statistic = NormalizedPlayerSeasonStatistic(
            source_code=EredivisieFantasyProvider.source_code,
            external_id=str(player_id),
            display_name=display_name.strip(),
            competition_code="EREDIVISIE",
            season_start_year=season_year,
            goals=_number(item.get("goals_scored")),
            starts=_number(item.get("starts")),
            minutes_played=_number(item.get("minutes")),
            assists=_number(item.get("assists")),
            yellow_cards=_number(item.get("yellow_cards")),
            red_cards=_number(item.get("red_cards")),
            penalties_scored=_number(item.get("penalties_scored")),
            clean_sheets=_number(item.get("clean_sheets")),
            goals_conceded=_number(item.get("goals_conceded")),
        )
        player, was_inserted, was_updated = upsert_player_statistic(
            session, statistic, competition_season_id=season.id
        )
        position = position_names.get(item.get("element_type"))
        if isinstance(position, str) and position.strip():
            player.position = position.strip()
        birth_text = item.get("birth_date")
        if isinstance(birth_text, str):
            try:
                player.birth_date = date.fromisoformat(birth_text[:10])
            except ValueError:
                pass
        if item.get("foot") in {"left", "right", "both"}:
            player.preferred_foot = str(item["foot"]).upper()
        nationality = item.get("nationality")
        if isinstance(nationality, str) and nationality.strip():
            player.nationality = nationality.strip()
        player.profile_source_id = source_id
        player.profile_updated_at = now
        endpoint_team_id = item.get("team")
        local_team_id = endpoint_to_local.get(str(endpoint_team_id))
        if local_team_id is not None:
            memberships = session.scalars(
                select(PlayerTeamSeason).where(
                    PlayerTeamSeason.player_id == player.id,
                    PlayerTeamSeason.competition_season_id == season.id,
                    PlayerTeamSeason.source_id == source_id,
                )
            ).all()
            membership = next(
                (row for row in memberships if row.team_id == local_team_id),
                memberships[0] if memberships else None,
            )
            for stale in memberships:
                if stale is not membership:
                    session.delete(stale)
            if membership is None:
                membership = PlayerTeamSeason(
                    player_id=player.id,
                    team_id=local_team_id,
                    competition_season_id=season.id,
                    source_id=source_id,
                )
            membership.position = str(position) if position else None
            membership.shirt_number = _number(item.get("squad_number"))
            membership.active = True
            membership.source_updated_at = now
            session.add(membership)
        inserted += int(was_inserted)
        updated += int(was_updated and not was_inserted)
        skipped += int(not was_inserted and not was_updated)
    return inserted, updated, skipped


def sync_eredivisie_fantasy_players(
    session_factory: SessionFactory,
    provider: EredivisieFantasyProvider | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and persist the active season's Eredivisie fantasy player pool."""
    provider = provider or EredivisieFantasyProvider()
    now = now or datetime.now(UTC)
    with session_factory.begin() as session:
        run = SyncRun(mode=SyncMode.DAILY.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id: UUID = run.id
        source_id = session.scalar(
            select(DataSource.id).where(DataSource.code == EredivisieFantasyProvider.source_code)
        )
        if source_id is None:
            raise ProviderError("Falta Fantasy Eredivisie en el catálogo de fuentes")
    counts = (0, 0, 0)
    status = SyncStatus.SUCCESS
    message = "Plantel y estadísticas actuales de Eredivisie sincronizados"
    try:
        payload = provider.get_bootstrap()
        with session_factory.begin() as session:
            counts = _sync_payload(session, payload, source_id=source_id, now=now)
    except Exception as error:
        status = SyncStatus.FAILED
        message = f"{error.__class__.__name__}: {str(error)[:700]}"
    with session_factory.begin() as session:
        competition_id = session.scalar(
            select(Competition.id).where(Competition.code == "EREDIVISIE")
        )
        session.add(
            SyncRunItem(
                sync_run_id=run_id,
                source_id=source_id,
                competition_id=competition_id,
                operation="EREDIVISIE_FANTASY_CURRENT_PLAYERS",
                status=status.value,
                requests_count=provider.request_count,
                inserted_count=counts[0],
                updated_count=counts[1],
                skipped_count=counts[2],
                error_count=int(status == SyncStatus.FAILED),
                error_code="EREDIVISIE_FANTASY_ERROR" if status == SyncStatus.FAILED else None,
                message=message if status == SyncStatus.FAILED else None,
            )
        )
        run = session.get(SyncRun, run_id)
        if run is not None:
            run.status = status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = provider.request_count
            run.inserted_count, run.updated_count, run.skipped_count = counts
            run.error_count = int(status == SyncStatus.FAILED)
            run.summary = message
    return {
        "run_id": run_id,
        "status": status,
        "requests_count": provider.request_count,
        "inserted_count": counts[0],
        "updated_count": counts[1],
        "skipped_count": counts[2],
        "error_count": int(status == SyncStatus.FAILED),
        "message": message,
    }
