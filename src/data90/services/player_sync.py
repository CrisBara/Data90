"""Import available seasonal scorer tables from OpenLigaDB."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.config.constants import COMPETITIONS
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Season,
    SyncRun,
    SyncRunItem,
)
from data90.domain.dto import NormalizedSeason
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderError
from data90.providers.openligadb import OpenLigaDBProvider
from data90.repositories.catalog import upsert_competition_season
from data90.repositories.players import upsert_player_statistic
from data90.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]

LEAGUE_NAMES = {
    "PL": ("Premier League",),
    "LALIGA": ("LaLiga", "LaLiga EA Sports", "La Liga", "La Liga (Spanien)"),
    "BUNDESLIGA": ("1. Fußball-Bundesliga", "Bundesliga"),
    "UEL": ("Europa League", "UEFA Europa League"),
}
SEASON_SUFFIX = re.compile(r"\s+\d{4}(?:[/\-]\d{2,4})?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PlayerSyncSummary:
    run_id: UUID
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _resolve_shortcut(
    provider: OpenLigaDBProvider,
    competition_code: str,
    season_year: int,
    leagues_by_year: dict[int, list[dict[str, Any]]],
) -> str | None:
    accepted_names = tuple(
        normalize_name(name) for name in LEAGUE_NAMES.get(competition_code, ())
    )
    if not accepted_names:
        return None
    leagues = leagues_by_year.get(season_year)
    if leagues is None:
        leagues = provider.get_available_leagues(season_year)
        leagues_by_year[season_year] = leagues
    # Prefer the most specific known league name. OpenLigaDB can expose both
    # the official league ("1. Fußball-Bundesliga") and a test alias
    # ("Bundesliga") for the same season; treating those as ambiguous skipped
    # the real scorer feed entirely.
    for accepted in accepted_names:
        matches = [
            league
            for league in leagues
            if str(league.get("leagueSeason", "")) == str(season_year)
            and normalize_name(SEASON_SUFFIX.sub("", league.get("leagueName") or ""))
            == accepted
        ]
        if len(matches) == 1:
            shortcut = matches[0].get("leagueShortcut")
            return str(shortcut) if shortcut else None
        if len(matches) > 1:
            return None
    return None


def sync_player_statistics(
    session_factory: SessionFactory,
    provider: OpenLigaDBProvider | None = None,
    *,
    competition_codes: tuple[str, ...] | None = None,
    season_year: int | None = None,
    now: datetime | None = None,
) -> PlayerSyncSummary:
    """Persist top-scorer data only for competitions served by OpenLigaDB."""
    provider = provider or OpenLigaDBProvider()
    now = now or datetime.now(UTC)
    source_code = provider.source_code
    definitions = {item.code: item for item in COMPETITIONS}
    selected = tuple(competition_codes or LEAGUE_NAMES)
    unknown = set(selected) - definitions.keys()
    if unknown:
        raise ValueError(f"Competiciones desconocidas: {', '.join(sorted(unknown))}")

    with session_factory.begin() as session:
        run = SyncRun(mode=SyncMode.MANUAL.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id
        source_id = session.scalar(select(DataSource.id).where(DataSource.code == source_code))
        if source_id is None:
            raise ProviderError("Falta OpenLigaDB en el catálogo de fuentes")

    leagues_by_year: dict[int, list[dict[str, Any]]] = {}
    inserted_total = updated_total = skipped_total = errors_total = 0
    item_results: list[tuple[int, int, int, int, int, int, str, str | None]] = []
    for code in selected:
        requests_before = provider.request_count
        inserted = updated = skipped = errors = 0
        message = None
        item_status = SyncStatus.SUCCESS.value
        try:
            with session_factory() as session:
                competition = session.scalar(select(Competition).where(Competition.code == code))
                season = (
                    session.scalar(
                        select(Season)
                        .join(CompetitionSeason, CompetitionSeason.season_id == Season.id)
                        .where(
                            CompetitionSeason.competition_id == competition.id,
                            Season.start_year == season_year if season_year is not None else True,
                        )
                        .order_by(Season.start_year.desc())
                        .limit(1)
                    )
                    if competition is not None
                    else None
                )
            if competition is None or season is None:
                skipped = 1
                message = "No hay temporada cargada para esta competición."
                item_status = "SKIPPED"
            else:
                shortcut = _resolve_shortcut(provider, code, season.start_year, leagues_by_year)
                if shortcut is None:
                    skipped = 1
                    message = "OpenLigaDB no ofrece una temporada única para esta competición."
                    item_status = "SKIPPED"
                else:
                    scorers = provider.get_goal_getters(code, season.start_year, shortcut)
                    with session_factory.begin() as session:
                        competition_season = upsert_competition_season(
                            session,
                            NormalizedSeason(
                                source_code=source_code,
                                competition_code=code,
                                external_id=None,
                                external_code=shortcut,
                                external_season=str(season.start_year),
                                start_year=season.start_year,
                                end_year=season.end_year,
                            ),
                        )
                        for scorer in scorers:
                            _, is_inserted, is_updated = upsert_player_statistic(
                                session,
                                scorer,
                                competition_season_id=competition_season.id,
                            )
                            inserted += int(is_inserted)
                            updated += int(is_updated)
                        skipped = len(scorers) - inserted - updated
        except Exception as error:
            item_status = SyncStatus.FAILED.value
            errors = 1
            message = f"{error.__class__.__name__}: {str(error)[:900]}"

        inserted_total += inserted
        updated_total += updated
        skipped_total += skipped
        errors_total += errors
        item_results.append(
            (
                source_id,
                definitions[code].code,
                provider.request_count - requests_before,
                inserted,
                updated,
                skipped,
                item_status,
                message,
            )
        )

    finished = datetime.now(UTC)
    status = (
        SyncStatus.FAILED
        if errors_total == len(selected)
        else SyncStatus.PARTIAL
        if errors_total
        else SyncStatus.SUCCESS
    )
    with session_factory.begin() as session:
        for (
            source_id,
            code,
            requests,
            inserted,
            updated,
            skipped,
            item_status,
            message,
        ) in item_results:
            competition_id = session.scalar(select(Competition.id).where(Competition.code == code))
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="OPENLIGADB_PLAYER_STATISTICS",
                    status=item_status,
                    requests_count=requests,
                    inserted_count=inserted,
                    updated_count=updated,
                    skipped_count=skipped,
                    error_count=int(item_status == SyncStatus.FAILED.value),
                    error_code="PROVIDER_OR_DATABASE_ERROR"
                    if item_status == SyncStatus.FAILED.value
                    else None,
                    message=message,
                )
            )
        run = session.get(SyncRun, run_id)
        run.finished_at = finished
        run.status = status.value
        run.requests_count = provider.request_count
        run.inserted_count = inserted_total
        run.updated_count = updated_total
        run.skipped_count = skipped_total
        run.error_count = errors_total
        run.summary = (
            "Goleadores OpenLigaDB sincronizados" if not errors_total else "Sincronización parcial"
        )

    return PlayerSyncSummary(
        run_id=run_id,
        status=status,
        requests_count=provider.request_count,
        inserted_count=inserted_total,
        updated_count=updated_total,
        skipped_count=skipped_total,
        error_count=errors_total,
        message="Goleadores OpenLigaDB sincronizados",
    )
