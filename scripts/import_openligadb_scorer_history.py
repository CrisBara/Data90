"""Import historical scorer tables that OpenLigaDB publishes for supported competitions."""

import argparse
from datetime import UTC, datetime

from sqlalchemy import select

from football_analytics.config.constants import COMPETITIONS
from football_analytics.database.models import (
    Competition,
    DataSource,
    Season,
    SyncRun,
    SyncRunItem,
)
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.dto import NormalizedSeason
from football_analytics.domain.enums import SyncMode, SyncStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.repositories.catalog import upsert_competition_season
from football_analytics.repositories.players import upsert_player_statistic
from football_analytics.services.player_sync import LEAGUE_NAMES, _resolve_shortcut

SCORER_COMPETITIONS = tuple(LEAGUE_NAMES)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-year", type=int, default=2010)
    parser.add_argument("--to-year", type=int, default=datetime.now(UTC).year - 1)
    args = parser.parse_args()
    if args.from_year > args.to_year:
        parser.error("--from-year debe ser menor o igual que --to-year")

    engine = make_engine()
    factory = make_session_factory(engine)
    provider = OpenLigaDBProvider()
    now = datetime.now(UTC)
    try:
        with factory.begin() as session:
            source = session.scalar(
                select(DataSource).where(DataSource.code == provider.source_code)
            )
            competitions = {
                item.code: session.scalar(select(Competition).where(Competition.code == item.code))
                for item in COMPETITIONS
                if item.code in SCORER_COMPETITIONS
            }
            if source is None or any(value is None for value in competitions.values()):
                parser.error("Falta el catálogo OpenLigaDB. Ejecuta bootstrap --catalog-only.")
            run = SyncRun(
                mode=SyncMode.MANUAL.value,
                status=SyncStatus.RUNNING.value,
                started_at=now,
                summary="Importación histórica de goleadores OpenLigaDB en curso",
            )
            session.add(run)
            session.flush()
            run_id = run.id
            source_id = source.id

        leagues_by_year: dict[int, list[dict]] = {}
        inserted_total = updated_total = skipped_total = error_total = success_count = 0
        items: list[tuple[int, int, str, int, int, int, int, str, str | None]] = []
        for year in range(args.from_year, args.to_year + 1):
            for code in SCORER_COMPETITIONS:
                before = provider.request_count
                inserted = updated = skipped = errors = 0
                status = SyncStatus.SUCCESS.value
                message = None
                competition = competitions[code]
                try:
                    shortcut = _resolve_shortcut(provider, code, year, leagues_by_year)
                    if shortcut is None:
                        status = "SKIPPED"
                        skipped = 1
                        message = "No hay una competición única con goleadores para este año."
                    else:
                        scorers = provider.get_goal_getters(code, year, shortcut)
                        if not scorers:
                            status = "SKIPPED"
                            skipped = 1
                            message = "La fuente no publicó filas de goleadores para este año."
                        else:
                            label = f"{year}/{(year + 1) % 100:02d}"
                            normalized_season = NormalizedSeason(
                                source_code=provider.source_code,
                                competition_code=code,
                                external_id=None,
                                external_code=shortcut,
                                external_season=label,
                                start_year=year,
                                end_year=year + 1,
                            )
                            with factory.begin() as session:
                                competition_season = upsert_competition_season(
                                    session, normalized_season
                                )
                                season = session.get(Season, competition_season.season_id)
                                if season is not None:
                                    season.active = year >= datetime.now(UTC).year
                                competition_season.status = (
                                    "ACTIVE" if year >= datetime.now(UTC).year else "COMPLETED"
                                )
                                competition_season.last_synced_at = now
                                for scorer in scorers:
                                    _, was_inserted, was_updated = upsert_player_statistic(
                                        session,
                                        scorer,
                                        competition_season_id=competition_season.id,
                                    )
                                    inserted += int(was_inserted)
                                    updated += int(was_updated)
                                skipped = len(scorers) - inserted - updated
                            success_count += 1
                except ProviderError as error:
                    if getattr(error, "status_code", None) == 404:
                        status = "SKIPPED"
                        skipped = 1
                        message = "OpenLigaDB no ofrece esta temporada."
                    else:
                        status = SyncStatus.FAILED.value
                        errors = 1
                        message = f"{error.__class__.__name__}: {str(error)[:850]}"
                except Exception as error:
                    status = SyncStatus.FAILED.value
                    errors = 1
                    message = f"{error.__class__.__name__}: {str(error)[:850]}"

                inserted_total += inserted
                updated_total += updated
                skipped_total += skipped
                error_total += errors
                items.append(
                    (
                        source_id,
                        competition.id,
                        code,
                        provider.request_count - before,
                        inserted,
                        updated,
                        skipped,
                        status,
                        message,
                    )
                )
                print(
                    f"{code} {year}/{(year + 1) % 100:02d}: {status}; "
                    f"{inserted} nuevos, {updated} actualizados, {skipped} sin datos/cambios"
                )

        status = (
            SyncStatus.FAILED
            if error_total and success_count == 0
            else SyncStatus.PARTIAL
            if error_total
            else SyncStatus.SUCCESS
        )
        with factory.begin() as session:
            for (
                item_source_id,
                competition_id,
                code,
                requests,
                inserted,
                updated,
                skipped,
                item_status,
                message,
            ) in items:
                session.add(
                    SyncRunItem(
                        sync_run_id=run_id,
                        source_id=item_source_id,
                        competition_id=competition_id,
                        operation="OPENLIGADB_HISTORICAL_SCORERS",
                        status=item_status,
                        requests_count=requests,
                        inserted_count=inserted,
                        updated_count=updated,
                        skipped_count=skipped,
                        error_count=int(item_status == SyncStatus.FAILED.value),
                        error_code="PROVIDER_OR_DATABASE_ERROR"
                        if item_status == SyncStatus.FAILED.value
                        else None,
                        message=f"{code} {message or ''}"[:1000],
                    )
                )
            run = session.get(SyncRun, run_id)
            run.status = status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = provider.request_count
            run.inserted_count = inserted_total
            run.updated_count = updated_total
            run.skipped_count = skipped_total
            run.error_count = error_total
            run.summary = (
                f"OpenLigaDB: {success_count} temporadas con goleadores, "
                f"{skipped_total} filas/temporadas sin cobertura o ya sincronizadas, "
                f"{error_total} errores"
            )
            summary = run.summary
        print(summary)
        print(f"Solicitudes HTTP: {provider.request_count}")
        return 2 if error_total else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
