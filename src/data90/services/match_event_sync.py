"""Synchronize individual goal events that can be linked to local matches."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.config.constants import COMPETITIONS
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchEvent,
    Season,
    SourceCompetitionSeason,
    SyncRun,
    SyncRunItem,
)
from data90.domain.dto import NormalizedTeam
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderError
from data90.providers.openligadb import OpenLigaDBProvider
from data90.repositories.catalog import upsert_team
from data90.repositories.players import upsert_source_player
from data90.sync.orchestrator import SyncSummary

SessionFactory = Callable[[], Session]
EVENT_COMPETITIONS = ("PL", "LALIGA", "BUNDESLIGA", "UEL")
MATCH_LINK_WINDOW_SECONDS = 36 * 60 * 60


def _team_dto(raw_team: dict[str, Any]) -> NormalizedTeam | None:
    team_id = raw_team.get("teamId")
    team_name = raw_team.get("teamName")
    if team_id is None or not isinstance(team_name, str) or not team_name.strip():
        return None
    short_name = raw_team.get("shortName")
    return NormalizedTeam(
        source_code="openligadb",
        external_id=str(team_id),
        canonical_name=team_name.strip(),
        short_name=short_name if isinstance(short_name, str) else None,
        country_name=None,
        crest_url=raw_team.get("teamIconUrl"),
        aliases=(short_name,) if isinstance(short_name, str) and short_name else (),
    )


def _kickoff(raw_match: dict[str, Any]) -> datetime | None:
    value = raw_match.get("matchDateTimeUTC")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _local_match_id(
    candidates: list[Any], *, home_id: int, away_id: int, kickoff: datetime
) -> int | None:
    matching = sorted(
        (
            (
                abs(
                    (
                        (
                            row.kickoff_utc.replace(tzinfo=UTC)
                            if row.kickoff_utc.tzinfo is None
                            else row.kickoff_utc
                        )
                        - kickoff
                    ).total_seconds()
                ),
                row.id,
            )
            for row in candidates
            if row.home_team_id == home_id and row.away_team_id == away_id
        ),
        key=lambda item: item[0],
    )
    if not matching or matching[0][0] > MATCH_LINK_WINDOW_SECONDS:
        return None
    if len(matching) > 1 and matching[1][0] - matching[0][0] < 60:
        return None
    return matching[0][1]


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def sync_match_goal_events(
    session_factory: SessionFactory,
    provider: OpenLigaDBProvider | None = None,
    *,
    season_year: int | None = None,
    competition_codes: tuple[str, ...] | None = None,
    mode: SyncMode = SyncMode.MANUAL,
    now: datetime | None = None,
) -> SyncSummary:
    """Import goal events only when the source match has a unique local match."""
    provider = provider or OpenLigaDBProvider()
    now = now or datetime.now(UTC)
    year = season_year or now.year
    selected = tuple(competition_codes or EVENT_COMPETITIONS)
    all_competitions = {item.code for item in COMPETITIONS}
    unknown = set(selected) - all_competitions
    if unknown:
        raise ValueError(f"Competición sin eventos OpenLigaDB: {', '.join(sorted(unknown))}")

    with session_factory.begin() as session:
        source_id = session.scalar(select(DataSource.id).where(DataSource.code == "openligadb"))
        if source_id is None:
            raise ProviderError("Falta OpenLigaDB en el catálogo de fuentes")
        run = SyncRun(mode=mode.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id

    inserted_total = skipped_total = errors_total = successful = 0
    for code in selected:
        requests_before = provider.request_count
        inserted = skipped = errors = 0
        item_status = SyncStatus.SUCCESS.value
        message = None
        with session_factory() as session:
            competition = session.scalar(select(Competition).where(Competition.code == code))
            competition_id = competition.id if competition is not None else None
            competition_season_id = (
                session.scalar(
                    select(CompetitionSeason.id)
                    .join(Season, Season.id == CompetitionSeason.season_id)
                    .where(
                        CompetitionSeason.competition_id == competition_id,
                        Season.start_year == year,
                    )
                )
                if competition_id is not None
                else None
            )
            shortcut = (
                session.scalar(
                    select(SourceCompetitionSeason.external_code)
                    .join(
                        CompetitionSeason,
                        CompetitionSeason.id == SourceCompetitionSeason.competition_season_id,
                    )
                    .join(Season, Season.id == CompetitionSeason.season_id)
                    .where(
                        SourceCompetitionSeason.source_id == source_id,
                        CompetitionSeason.competition_id == competition_id,
                        Season.start_year == year,
                    )
                )
                if competition_id is not None
                else None
            )
            local_matches = (
                session.scalars(
                    select(Match).where(Match.competition_season_id == competition_season_id)
                ).all()
                if competition_season_id is not None
                else []
            )
        if code not in EVENT_COMPETITIONS:
            item_status = "SKIPPED"
            skipped = 1
            message = "OpenLigaDB no ofrece eventos individuales para esta competición."
        elif competition_id is None or competition_season_id is None or not shortcut:
            item_status = "SKIPPED"
            skipped = 1
            message = "No hay temporada o shortcut OpenLigaDB para esta competición."
        else:
            try:
                raw_matches = provider.get_match_data(shortcut, year)
                with session_factory.begin() as session:
                    team_ids: dict[str, int] = {}
                    for raw_match in raw_matches:
                        for key in ("team1", "team2"):
                            dto = _team_dto(raw_match.get(key) or {})
                            if dto is not None and dto.external_id not in team_ids:
                                team_ids[dto.external_id] = upsert_team(session, dto).id

                    existing_external_ids = set(
                        session.scalars(
                            select(MatchEvent.external_id).where(
                                MatchEvent.source_id == source_id,
                                MatchEvent.external_id.is_not(None),
                            )
                        ).all()
                    )
                    for raw_match in raw_matches:
                        match_external_id = str(raw_match.get("matchID", ""))
                        kickoff = _kickoff(raw_match)
                        home_raw = raw_match.get("team1") or {}
                        away_raw = raw_match.get("team2") or {}
                        home_id = team_ids.get(str(home_raw.get("teamId", "")))
                        away_id = team_ids.get(str(away_raw.get("teamId", "")))
                        local_id = (
                            _local_match_id(
                                local_matches,
                                home_id=home_id,
                                away_id=away_id,
                                kickoff=kickoff,
                            )
                            if home_id is not None and away_id is not None and kickoff
                            else None
                        )
                        goals = raw_match.get("goals") or []
                        if not isinstance(goals, list):
                            skipped += 1
                            continue
                        for goal in goals:
                            if not isinstance(goal, dict):
                                skipped += 1
                                continue
                            goal_id = goal.get("goalID")
                            player_external_id = goal.get("goalGetterID")
                            player_name = goal.get("goalGetterName")
                            if (
                                not match_external_id
                                or goal_id is None
                                or player_external_id in (None, 0, "0")
                                or not isinstance(player_name, str)
                                or not player_name.strip()
                            ):
                                skipped += 1
                                continue
                            event_external_id = f"{match_external_id}:{goal_id}"
                            if event_external_id in existing_external_ids:
                                skipped += 1
                                continue
                            if local_id is None:
                                skipped += 1
                                continue
                            scoring_team_id = team_ids.get(str(goal.get("scoringTeamId")))
                            player, _, _ = upsert_source_player(
                                session,
                                source_code="openligadb",
                                external_id=str(player_external_id),
                                display_name=player_name,
                            )
                            session.add(
                                MatchEvent(
                                    match_id=local_id,
                                    team_id=scoring_team_id,
                                    event_type=(
                                        "OWN_GOAL"
                                        if goal.get("isOwnGoal")
                                        else "PENALTY_GOAL"
                                        if goal.get("isPenalty")
                                        else "GOAL"
                                    ),
                                    minute=_int_or_none(goal.get("matchMinute")),
                                    player_name=player_name.strip(),
                                    player_id=player.id,
                                    source_id=source_id,
                                    external_id=event_external_id,
                                )
                            )
                            existing_external_ids.add(event_external_id)
                            inserted += 1
                successful += 1
            except ProviderError as error:
                if getattr(error, "status_code", None) == 404:
                    item_status = "SKIPPED"
                    skipped = 1
                    message = "OpenLigaDB no publicó eventos para esta temporada."
                else:
                    item_status = SyncStatus.FAILED.value
                    errors = 1
                    message = f"{error.__class__.__name__}: {str(error)[:850]}"
            except Exception as error:
                item_status = SyncStatus.FAILED.value
                errors = 1
                message = f"{error.__class__.__name__}: {str(error)[:850]}"

        inserted_total += inserted
        skipped_total += skipped
        errors_total += errors
        with session_factory.begin() as session:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="OPENLIGADB_MATCH_EVENTS",
                    status=item_status,
                    requests_count=provider.request_count - requests_before,
                    inserted_count=inserted,
                    skipped_count=skipped,
                    error_count=errors,
                    error_code="PROVIDER_OR_DATABASE_ERROR" if errors else None,
                    message=f"{code} {year}: {message or ''}"[:1000],
                )
            )

    final_status = (
        SyncStatus.FAILED
        if errors_total and successful == 0
        else SyncStatus.PARTIAL
        if errors_total
        else SyncStatus.SUCCESS
    )
    finished = datetime.now(UTC)
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        run.status = final_status.value
        run.finished_at = finished
        run.requests_count = provider.request_count
        run.inserted_count = inserted_total
        run.skipped_count = skipped_total
        run.error_count = errors_total
        run.summary = (
            f"OpenLigaDB: {inserted_total} eventos de gol vinculados, "
            f"{skipped_total} omitidos, {errors_total} errores"
        )
        message = run.summary
    return SyncSummary(
        run_id=run_id,
        status=final_status,
        requests_count=provider.request_count,
        inserted_count=inserted_total,
        updated_count=0,
        skipped_count=skipped_total,
        conflict_count=0,
        error_count=errors_total,
        message=message,
    )
