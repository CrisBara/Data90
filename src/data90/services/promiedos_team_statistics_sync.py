"""Import current team aggregates from Promiedos' public league pages."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Season,
    SyncRun,
    SyncRunItem,
    TeamCompetitionSeasonStatistic,
)
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderError
from data90.providers.promiedos import PromiedosProvider
from data90.services.promiedos_sync import _team_index, _team_source_mappings

SessionFactory = Callable[[], Session]

PROMIEDOS_TEAM_STATS_ROUTES = {
    "PL": ("premier-league", "h"),
    "LALIGA": ("laliga", "bb"),
    "SERIE_A": ("serie-a", "bh"),
    "BUNDESLIGA": ("bundesliga", "cf"),
    "LIGUE_1": ("ligue-1", "df"),
    "UCL": ("uefa-champions-league", "fhc"),
    "UEL": ("uefa-europa-league", "fhd"),
}

STATISTIC_FIELDS = {
    "GoalsAverage": "goals_average",
    "GoalsConcededAverage": "goals_conceded_average",
    "CleanSheets": "clean_sheets",
    "PossessionPercentage": "possession_percentage",
    "CornersAverage": "corners_average",
    "RedCards": "red_cards",
    "YellowCards": "yellow_cards",
}


@dataclass(frozen=True, slots=True)
class PromiedosTeamStatsSummary:
    run_id: UUID | None
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _number(value: Any) -> Decimal | None:
    if not isinstance(value, str | int | float):
        return None
    cleaned = str(value).strip().replace("%", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _penalties(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _table_values(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for table in data.get("stats", {}).get("tables", []):
        if not isinstance(table, dict):
            continue
        columns = table.get("columns")
        field = (
            STATISTIC_FIELDS.get(str(columns[0].get("key")))
            if isinstance(columns, list) and columns and isinstance(columns[0], dict)
            else None
        )
        if field is None and isinstance(columns, list) and columns:
            key = str(columns[0].get("key"))
            if key in {"PenaltyGoals", "PenaltiesConceded"}:
                field = key
        if field is None:
            continue
        for row in table.get("rows", []):
            entity = row.get("entity") if isinstance(row, dict) else None
            team = entity.get("object") if isinstance(entity, dict) else None
            external_id = team.get("id") if isinstance(team, dict) else None
            row_values = row.get("values") if isinstance(row, dict) else None
            value = (
                row_values[0].get("value")
                if isinstance(row_values, list) and row_values and isinstance(row_values[0], dict)
                else None
            )
            if external_id is None or value is None:
                continue
            destination = values.setdefault(str(external_id), {})
            if field in {"PenaltyGoals", "PenaltiesConceded"}:
                parsed = _penalties(value)
                if parsed:
                    if field == "PenaltyGoals":
                        destination["penalties_scored"], destination["penalties_attempted"] = parsed
                    else:
                        (
                            destination["penalties_conceded"],
                            destination["penalties_conceded_attempted"],
                        ) = parsed
            elif field in {"clean_sheets", "red_cards", "yellow_cards"}:
                number = _number(value)
                if number is not None and number == number.to_integral_value():
                    destination[field] = int(number)
            else:
                number = _number(value)
                if number is not None:
                    destination[field] = number
    return values


def sync_promiedos_team_statistics(
    session_factory: SessionFactory,
    provider: PromiedosProvider | None = None,
    *,
    competition_codes: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> PromiedosTeamStatsSummary:
    """Persist current competition-wide team aggregates without fabricating match splits."""
    provider = provider or PromiedosProvider()
    now = now or datetime.now(UTC)
    selected = {
        code: route
        for code, route in PROMIEDOS_TEAM_STATS_ROUTES.items()
        if competition_codes is None or code in competition_codes
    }
    with session_factory.begin() as session:
        run = SyncRun(mode=SyncMode.DAILY.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id
        source_id = session.scalar(
            select(DataSource.id).where(DataSource.code == provider.source_code)
        )
        if source_id is None:
            raise ProviderError("Falta Promiedos en el catálogo de fuentes; ejecutá el bootstrap")

    inserted_total = updated_total = skipped_total = error_total = 0
    failures: list[str] = []
    for code, (slug, league_id) in selected.items():
        request_start = provider.request_count
        inserted = updated = skipped = 0
        error_message = None
        try:
            data = provider.get_team_statistics(slug, league_id)
            source_teams = [
                {"entity": {"object": item["team"]}}
                for item in data.get("teams", [])
                if isinstance(item, dict) and isinstance(item.get("team"), dict)
            ]
            with session_factory.begin() as session:
                competition = session.scalar(select(Competition).where(Competition.code == code))
                season = session.scalar(select(Season).where(Season.start_year == now.year))
                competition_season = (
                    session.scalar(
                        select(CompetitionSeason).where(
                            CompetitionSeason.competition_id == competition.id,
                            CompetitionSeason.season_id == season.id,
                        )
                    )
                    if competition and season
                    else None
                )
                if competition_season is None:
                    raise ProviderError(f"No está inicializada la temporada actual de {code}")
                team_ids = _team_source_mappings(
                    session,
                    source_id,
                    competition_season.id,
                    _team_index(session),
                    source_teams,
                )
                stats_by_external_id = _table_values(data)
                for external_id, values in stats_by_external_id.items():
                    team_id = team_ids.get(external_id)
                    if team_id is None:
                        skipped += 1
                        continue
                    statistic = session.scalar(
                        select(TeamCompetitionSeasonStatistic).where(
                            TeamCompetitionSeasonStatistic.team_id == team_id,
                            TeamCompetitionSeasonStatistic.competition_season_id
                            == competition_season.id,
                            TeamCompetitionSeasonStatistic.source_id == source_id,
                        )
                    )
                    was_inserted = statistic is None
                    if statistic is None:
                        statistic = TeamCompetitionSeasonStatistic(
                            team_id=team_id,
                            competition_season_id=competition_season.id,
                            source_id=source_id,
                        )
                    changed = False
                    for field, value in values.items():
                        if getattr(statistic, field) != value:
                            setattr(statistic, field, value)
                            changed = True
                    statistic.source_updated_at = now
                    session.add(statistic)
                    inserted += int(was_inserted)
                    updated += int(not was_inserted and changed)
                    skipped += int(not was_inserted and not changed)
        except Exception as error:
            error_total += 1
            failures.append(code)
            error_message = f"{error.__class__.__name__}: {str(error)[:700]}"

        inserted_total += inserted
        updated_total += updated
        skipped_total += skipped
        with session_factory.begin() as session:
            competition_id = session.scalar(select(Competition.id).where(Competition.code == code))
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="PROMIEDOS_TEAM_SEASON_STATISTICS",
                    status=SyncStatus.FAILED.value if error_message else SyncStatus.SUCCESS.value,
                    requests_count=provider.request_count - request_start,
                    inserted_count=inserted,
                    updated_count=updated,
                    skipped_count=skipped,
                    error_count=int(error_message is not None),
                    error_code="PROMIEDOS_TEAM_STATS" if error_message else None,
                    message=error_message,
                )
            )

    status = (
        SyncStatus.FAILED
        if error_total == len(selected)
        else SyncStatus.PARTIAL
        if error_total
        else SyncStatus.SUCCESS
    )
    message = (
        "Estadísticas agregadas de equipos de Promiedos sincronizadas"
        if not failures
        else f"Sincronización parcial; revisar: {', '.join(failures)}"
    )
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        if run is not None:
            run.status = status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = provider.request_count
            run.inserted_count = inserted_total
            run.updated_count = updated_total
            run.skipped_count = skipped_total
            run.error_count = error_total
            run.summary = message
    return PromiedosTeamStatsSummary(
        run_id,
        status,
        provider.request_count,
        inserted_total,
        updated_total,
        skipped_total,
        error_total,
        message,
    )
