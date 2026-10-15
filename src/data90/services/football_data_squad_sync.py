"""Sync full current club squads and player profiles from football-data.org."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.config.constants import COMPETITIONS
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    PlayerTeamSeason,
    Season,
    SourceTeam,
    SyncRun,
    SyncRunItem,
    Team,
    TeamCoach,
)
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderError
from data90.providers.football_data import FootballDataProvider
from data90.repositories.players import upsert_source_player

SessionFactory = Callable[[], Session]


def _current_teams(session: Session, source_id: int) -> list[dict[str, Any]]:
    supported_codes = {
        item.code
        for item in COMPETITIONS
        if any(
            source_code == FootballDataProvider.source_code
            for source_code, _ in item.provider_codes
        )
    }
    rows = session.execute(
        select(
            SourceTeam.external_id,
            SourceTeam.team_id,
            Team.canonical_name,
            CompetitionSeason.id,
            Competition.id,
            Competition.name,
        )
        .join(Team, Team.id == SourceTeam.team_id)
        .join(Match, (Match.home_team_id == Team.id) | (Match.away_team_id == Team.id))
        .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .where(
            SourceTeam.source_id == source_id,
            Competition.code.in_(supported_codes),
            Season.active.is_(True),
        )
        .distinct()
        .order_by(Team.canonical_name)
    ).all()
    teams: dict[str, dict[str, Any]] = {}
    for external_id, team_id, team_name, season_id, competition_id, competition_name in rows:
        item = teams.setdefault(
            external_id,
            {
                "external_id": external_id,
                "team_id": team_id,
                "team_name": team_name,
                "seasons": {},
                "competition_names": set(),
            },
        )
        item["seasons"][season_id] = competition_id
        item["competition_names"].add(competition_name)
    return list(teams.values())


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _save_squad(
    session: Session,
    *,
    team: dict[str, Any],
    squad: list[dict[str, Any]],
    source_id: int,
    now: datetime,
    coach: dict[str, Any] | None = None,
) -> tuple[int, int, int]:
    inserted = updated = skipped = 0
    seen_player_ids: set[int] = set()
    for profile in squad:
        external_id = profile.get("id")
        display_name = profile.get("name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = " ".join(
                value.strip()
                for value in (profile.get("firstName"), profile.get("lastName"))
                if isinstance(value, str) and value.strip()
            )
        if external_id is None or not display_name:
            skipped += 1
            continue
        player, was_inserted, was_updated = upsert_source_player(
            session,
            source_code=FootballDataProvider.source_code,
            external_id=str(external_id),
            display_name=display_name,
        )
        seen_player_ids.add(player.id)
        nationality = profile.get("nationality")
        if isinstance(nationality, str) and nationality.strip():
            player.nationality = nationality.strip()
        position = profile.get("position")
        if isinstance(position, str) and position.strip():
            player.position = position.strip()
        player.birth_date = _as_date(profile.get("dateOfBirth")) or player.birth_date
        player.profile_source_id = source_id
        player.profile_updated_at = now
        shirt_number = profile.get("shirtNumber")
        for season_id in team["seasons"]:
            membership = session.scalar(
                select(PlayerTeamSeason).where(
                    PlayerTeamSeason.player_id == player.id,
                    PlayerTeamSeason.team_id == team["team_id"],
                    PlayerTeamSeason.competition_season_id == season_id,
                    PlayerTeamSeason.source_id == source_id,
                )
            )
            if membership is None:
                membership = PlayerTeamSeason(
                    player_id=player.id,
                    team_id=team["team_id"],
                    competition_season_id=season_id,
                    source_id=source_id,
                )
            membership.position = position.strip() if isinstance(position, str) else None
            membership.shirt_number = (
                shirt_number
                if isinstance(shirt_number, int) and not isinstance(shirt_number, bool)
                else None
            )
            membership.active = True
            membership.source_updated_at = now
            session.add(membership)
        inserted += int(was_inserted)
        updated += int(was_updated and not was_inserted)
        skipped += int(not was_inserted and not was_updated)

    if isinstance(coach, dict):
        coach_name = coach.get("name")
        if not isinstance(coach_name, str) or not coach_name.strip():
            coach_name = " ".join(
                part.strip()
                for part in (coach.get("firstName"), coach.get("lastName"))
                if isinstance(part, str) and part.strip()
            )
        if coach_name:
            coach_row = session.scalar(
                select(TeamCoach).where(
                    TeamCoach.team_id == team["team_id"], TeamCoach.source_id == source_id
                )
            )
            if coach_row is None:
                coach_row = TeamCoach(
                    team_id=team["team_id"], source_id=source_id, full_name=coach_name.strip()
                )
                session.add(coach_row)
            coach_row.full_name = coach_name.strip()
            coach_row.external_id = str(coach.get("id")) if coach.get("id") is not None else None
            coach_row.source_updated_at = now

    for season_id in team["seasons"]:
        stale_memberships = session.scalars(
            select(PlayerTeamSeason).where(
                PlayerTeamSeason.team_id == team["team_id"],
                PlayerTeamSeason.competition_season_id == season_id,
                PlayerTeamSeason.source_id == source_id,
                PlayerTeamSeason.active.is_(True),
            )
        ).all()
        for membership in stale_memberships:
            if membership.player_id not in seen_player_ids:
                membership.active = False
    return inserted, updated, skipped


def sync_current_football_data_squads(
    session_factory: SessionFactory,
    provider: FootballDataProvider | None = None,
    *,
    now: datetime | None = None,
    competition_codes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Refresh full current rosters, nationality, and team memberships."""
    provider = provider or FootballDataProvider()
    now = now or datetime.now(UTC)
    with session_factory.begin() as session:
        source_id = session.scalar(
            select(DataSource.id).where(DataSource.code == FootballDataProvider.source_code)
        )
        if source_id is None:
            raise ProviderError("Falta football-data.org en el catálogo de fuentes")
        teams = _current_teams(session, source_id)
        if competition_codes:
            allowed = set(competition_codes)
            competition_codes_by_id = dict(
                session.execute(select(Competition.id, Competition.code)).all()
            )
            teams = [
                team
                for team in teams
                if any(
                    competition_codes_by_id.get(competition_id) in allowed
                    for competition_id in team["seasons"].values()
                )
            ]
        run = SyncRun(
            mode=SyncMode.DAILY.value,
            status=SyncStatus.RUNNING.value,
            started_at=now,
        )
        session.add(run)
        session.flush()
        run_id: UUID = run.id

    inserted_total = updated_total = skipped_total = errors_total = requests_total = 0
    skipped_teams = 0
    errors: list[str] = []
    for team in teams:
        requests_before = provider.request_count
        counts = (0, 0, 0)
        status = SyncStatus.SUCCESS.value
        error_message = None
        try:
            data = provider.get_team_details(team["external_id"])
            squad = [row for row in data["squad"] if isinstance(row, dict)]
            if not squad:
                status = "SKIPPED"
                skipped_teams += 1
                counts = (0, 0, 1)
                error_message = f"{team['team_name']}: la fuente no publicó el plantel"
            else:
                with session_factory.begin() as session:
                    counts = _save_squad(
                        session,
                        team=team,
                        squad=squad,
                        source_id=source_id,
                        now=now,
                        coach=data.get("coach") if isinstance(data.get("coach"), dict) else None,
                    )
        except Exception as error:
            status = SyncStatus.FAILED.value
            errors_total += 1
            errors.append(team["team_name"])
            error_message = f"{error.__class__.__name__}: {str(error)[:500]}"
        inserted_total += counts[0]
        updated_total += counts[1]
        skipped_total += counts[2]
        request_count = provider.request_count - requests_before
        requests_total += request_count
        with session_factory.begin() as session:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=min(team["seasons"].values(), default=None),
                    operation="FOOTBALL_DATA_CURRENT_SQUAD",
                    status=status,
                    requests_count=request_count,
                    inserted_count=counts[0],
                    updated_count=counts[1],
                    skipped_count=counts[2],
                    error_count=int(status == SyncStatus.FAILED.value),
                    error_code="FOOTBALL_DATA_SQUAD_ERROR"
                    if status == SyncStatus.FAILED.value
                    else None,
                    message=error_message,
                )
            )

    final_status = (
        SyncStatus.FAILED
        if errors_total == len(teams) and teams
        else SyncStatus.PARTIAL
        if errors_total
        else SyncStatus.SUCCESS
    )
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        if run is not None:
            run.status = final_status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = requests_total
            run.inserted_count = inserted_total
            run.updated_count = updated_total
            run.skipped_count = skipped_total
            run.error_count = errors_total
            run.summary = (
                f"Planteles publicados: {len(teams) - skipped_teams} clubes; "
                f"{skipped_teams} clubes sin plantel publicado"
                if not errors
                else f"Clubes con error: {', '.join(errors[:30])}"
            )
    return {
        "run_id": run_id,
        "status": final_status,
        "requests_count": requests_total,
        "teams_count": len(teams),
        "inserted_count": inserted_total,
        "updated_count": updated_total,
        "skipped_count": skipped_total,
        "error_count": errors_total,
        "message": (
            f"Planteles publicados de {len(teams) - skipped_teams} clubes; "
            f"{skipped_teams} sin nómina publicada; {errors_total} errores"
        ),
    }
