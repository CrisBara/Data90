from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.config.constants import COMPETITIONS
from data90.config.settings import get_settings
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Season,
    SourceCompetitionSeason,
    SyncRun,
    SyncRunItem,
)
from data90.domain.dto import NormalizedSeason, NormalizedTeam
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import EntityResolutionError, ProviderError
from data90.providers.football_data import FootballDataProvider
from data90.providers.openligadb import OpenLigaDBProvider
from data90.repositories.catalog import upsert_competition_season, upsert_team
from data90.repositories.match_sync import upsert_match
from data90.repositories.standings import add_standings_snapshot

SessionFactory = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class SyncSummary:
    run_id: UUID
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    conflict_count: int
    error_count: int
    message: str


def _window(
    mode: SyncMode, season: NormalizedSeason, now: datetime
) -> tuple[date | None, date | None]:
    local_now = now.astimezone(ZoneInfo(get_settings().app_timezone))
    if mode in (SyncMode.BOOTSTRAP, SyncMode.RECONCILIATION):
        if season.start_date is None or season.end_date is None:
            return None, None
        return season.start_date, season.end_date
    return local_now.date() - timedelta(days=2), local_now.date() + timedelta(days=7)


def _openligadb_season(
    session: Session,
    provider: OpenLigaDBProvider,
    competition_code: str,
    season_start_year: int,
) -> tuple[NormalizedSeason, str]:
    source = session.scalar(select(DataSource).where(DataSource.code == provider.source_code))
    competition = session.scalar(select(Competition).where(Competition.code == competition_code))
    existing_shortcut = None
    if source is not None and competition is not None:
        existing_shortcut = session.scalar(
            select(SourceCompetitionSeason.external_code)
            .join(
                CompetitionSeason,
                SourceCompetitionSeason.competition_season_id == CompetitionSeason.id,
            )
            .join(Season, CompetitionSeason.season_id == Season.id)
            .where(
                SourceCompetitionSeason.source_id == source.id,
                CompetitionSeason.competition_id == competition.id,
                Season.start_year == season_start_year,
            )
        )
    shortcut = existing_shortcut or provider.validate_league_shortcut(
        ("UEFA Europa League", "Europa League"), season_start_year
    )
    return (
        NormalizedSeason(
            source_code=provider.source_code,
            competition_code=competition_code,
            external_id=None,
            external_code=shortcut,
            external_season=str(season_start_year),
            start_year=season_start_year,
            end_year=season_start_year + 1,
        ),
        shortcut,
    )


def _season_year_for_openligadb(
    session: Session, current_year: int, football_data_years: Mapping[str, int]
) -> int:
    year = football_data_years.get("PL")
    if year is not None:
        return year
    prior = session.scalar(
        select(Season.start_year)
        .where(Season.active.is_(True))
        .order_by(Season.start_year.desc())
        .limit(1)
    )
    return int(prior) if prior is not None else current_year


def _team_map_for_competition(
    session: Session,
    source_code: str,
    provider: FootballDataProvider | OpenLigaDBProvider,
    competition_code: str,
    season: NormalizedSeason,
    shortcut: str | None,
) -> dict[str, int]:
    if isinstance(provider, OpenLigaDBProvider):
        team_dtos = provider.get_teams(shortcut or "", season.start_year)
    else:
        team_dtos = provider.get_teams(competition_code, season.start_year)
    result = {}
    for team_dto in team_dtos:
        team_id = upsert_team(session, team_dto)
        result[team_dto.external_id] = team_id.id
    return result


def _persist_standings(
    session: Session,
    provider: FootballDataProvider | OpenLigaDBProvider,
    competition_code: str,
    season: NormalizedSeason,
    shortcut: str | None,
    competition_season_id: int,
    source_id: int,
    team_ids: dict[str, int],
) -> int:
    if isinstance(provider, OpenLigaDBProvider):
        rows = list(provider.get_standings(competition_code, season.start_year, shortcut or ""))
    else:
        rows = list(provider.get_standings(competition_code, season.start_year))
    grouped: dict[tuple[str, str | None, int | None], list] = defaultdict(list)
    for row in rows:
        grouped[(row.scope, row.group_name, row.matchday)].append(row)
    inserted = 0
    for group in grouped.values():
        inserted += add_standings_snapshot(
            session,
            competition_season_id=competition_season_id,
            source_id=source_id,
            team_ids_by_external_id=team_ids,
            rows=group,
        )
    return inserted


def run_sync(
    session_factory: SessionFactory,
    providers: Mapping[str, FootballDataProvider | OpenLigaDBProvider],
    mode: SyncMode,
    *,
    now: datetime | None = None,
    competition_codes: tuple[str, ...] | None = None,
) -> SyncSummary:
    """Sync each configured competition in its own transaction and retain partial progress."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    known_codes = {definition.code for definition in COMPETITIONS}
    unknown_codes = set(competition_codes or ()) - known_codes
    if unknown_codes:
        raise ValueError(f"Competiciones desconocidas: {', '.join(sorted(unknown_codes))}")
    definitions = tuple(
        definition
        for definition in COMPETITIONS
        if competition_codes is None or definition.code in competition_codes
    )
    if not definitions:
        raise ValueError("Debe seleccionarse al menos una competición")
    run_id = None
    with session_factory.begin() as session:
        run = SyncRun(mode=mode.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id

    failures = 0
    requests_count = 0
    inserted_count = 0
    updated_count = 0
    skipped_count = 0
    conflict_count = 0
    errors: list[str] = []
    football_data_years: dict[str, int] = {}
    local_year = now.astimezone(ZoneInfo(settings.app_timezone)).year

    for definition in definitions:
        source_code = definition.provider_codes[0][0]
        provider = providers.get(source_code)
        item_requests_before = getattr(provider, "request_count", 0) if provider else 0
        inserted_before = inserted_count
        updated_before = updated_count
        skipped_before = skipped_count
        item_status = "SUCCESS"
        message: str | None = None
        error_code: str | None = None
        competition_id: int | None = None
        try:
            if provider is None:
                raise ProviderError(f"No hay adaptador activo para {source_code}")
            with session_factory.begin() as session:
                competition = session.scalar(
                    select(Competition).where(Competition.code == definition.code)
                )
                source = session.scalar(select(DataSource).where(DataSource.code == source_code))
                if competition is None or source is None:
                    raise EntityResolutionError("Falta el catálogo inicial de competición o fuente")
                competition_id = competition.id

                shortcut = None
                if isinstance(provider, FootballDataProvider):
                    season = provider.get_current_season(definition.code)
                    football_data_years[definition.code] = season.start_year
                else:
                    year = _season_year_for_openligadb(session, local_year, football_data_years)
                    season, shortcut = _openligadb_season(session, provider, definition.code, year)

                competition_season = upsert_competition_season(session, season)
                start_date, end_date = _window(mode, season, now)
                if isinstance(provider, FootballDataProvider) and (
                    start_date is None or end_date is None
                ):
                    raise ProviderError("La fuente no publicó el rango de fechas de la temporada")
                team_ids = _team_map_for_competition(
                    session,
                    source_code,
                    provider,
                    definition.code,
                    season,
                    shortcut,
                )

                if isinstance(provider, OpenLigaDBProvider):
                    source_season_mapping = session.scalar(
                        select(SourceCompetitionSeason).where(
                            SourceCompetitionSeason.source_id == source.id,
                            SourceCompetitionSeason.competition_season_id == competition_season.id,
                        )
                    )
                    if source_season_mapping is None:
                        raise EntityResolutionError("Falta el mapping de temporada OpenLigaDB")
                    if mode in (SyncMode.DAILY, SyncMode.MANUAL):
                        metadata = dict(source_season_mapping.metadata_json or {})
                        known_markers = dict(metadata.get("last_changed_by_group", {}))
                        matches, new_markers, unchanged_groups = provider.get_changed_matches(
                            definition.code,
                            season.start_year,
                            shortcut or "",
                            known_markers,
                        )
                        known_markers.update(new_markers)
                        metadata["last_changed_by_group"] = known_markers
                        source_season_mapping.metadata_json = metadata
                        skipped_count += unchanged_groups
                    else:
                        matches = provider.get_matches(
                            definition.code,
                            season.start_year,
                            shortcut or "",
                            start_date,
                            end_date,
                        )
                else:
                    matches = provider.get_matches(
                        definition.code, season.start_year, start_date, end_date
                    )

                for match_dto in matches:
                    home_id = team_ids.get(match_dto.home_external_id)
                    away_id = team_ids.get(match_dto.away_external_id)
                    if home_id is None:
                        home_id = upsert_team(
                            session,
                            NormalizedTeam(
                                source_code,
                                match_dto.home_external_id,
                                match_dto.home_name,
                            ),
                        ).id
                        team_ids[match_dto.home_external_id] = home_id
                    if away_id is None:
                        away_id = upsert_team(
                            session,
                            NormalizedTeam(
                                source_code,
                                match_dto.away_external_id,
                                match_dto.away_name,
                            ),
                        ).id
                        team_ids[match_dto.away_external_id] = away_id
                    result = upsert_match(
                        session,
                        match_dto,
                        competition_season_id=competition_season.id,
                        home_team_id=home_id,
                        away_team_id=away_id,
                    )
                    if result.inserted:
                        inserted_count += 1
                    elif result.updated_fields:
                        updated_count += 1
                    else:
                        skipped_count += 1
                    conflict_count += len(result.conflict_fields)

                _persist_standings(
                    session,
                    provider,
                    definition.code,
                    season,
                    shortcut,
                    competition_season.id,
                    source.id,
                    team_ids,
                )
                competition_season.last_synced_at = now
        except Exception as error:
            failures += 1
            item_status = "ERROR"
            error_code = error.__class__.__name__
            message = str(error)[:1000]
            errors.append(f"{definition.code}: {error.__class__.__name__}")

        requests_delta = (
            getattr(provider, "request_count", item_requests_before) - item_requests_before
            if provider
            else 0
        )
        requests_count += requests_delta
        with session_factory.begin() as session:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=_get_source_id(session, source_code),
                    competition_id=competition_id,
                    operation=mode.value,
                    status=item_status,
                    requests_count=requests_delta,
                    inserted_count=inserted_count - inserted_before,
                    updated_count=updated_count - updated_before,
                    skipped_count=skipped_count - skipped_before,
                    error_count=1 if item_status == "ERROR" else 0,
                    error_code=error_code,
                    message=message,
                )
            )

    status = (
        SyncStatus.FAILED
        if failures == len(definitions)
        else SyncStatus.PARTIAL
        if failures
        else SyncStatus.SUCCESS
    )
    summary = "; ".join(errors) if errors else "Sincronización completada"
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        if run is not None:
            run.status = status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = requests_count
            run.inserted_count = inserted_count
            run.updated_count = updated_count
            run.skipped_count = skipped_count
            run.conflict_count = conflict_count
            run.error_count = failures
            run.summary = summary
    return SyncSummary(
        run_id=run_id,
        status=status,
        requests_count=requests_count,
        inserted_count=inserted_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        conflict_count=conflict_count,
        error_count=failures,
        message=summary,
    )


def _get_source_id(session: Session, source_code: str) -> int:
    source_id = session.scalar(select(DataSource.id).where(DataSource.code == source_code))
    if source_id is None:
        raise EntityResolutionError(f"Fuente desconocida: {source_code}")
    return source_id
