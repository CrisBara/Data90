"""Manually import one historical domestic league season from OpenFootball."""

import argparse
from datetime import UTC, datetime

from sqlalchemy import select

from football_analytics.database.models import (
    Competition,
    DataSource,
    SourceCompetition,
    SyncRun,
    SyncRunItem,
)
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.dto import NormalizedSeason
from football_analytics.domain.enums import SyncMode, SyncStatus
from football_analytics.domain.exceptions import EntityResolutionError
from football_analytics.providers.openfootball import (
    COMPETITION_TIMEZONES,
    OpenFootballProvider,
)
from football_analytics.repositories.catalog import upsert_competition_season, upsert_team
from football_analytics.repositories.match_sync import upsert_match


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--competition", choices=tuple(COMPETITION_TIMEZONES), required=True)
    parser.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2025")
    args = parser.parse_args()

    engine = make_engine()
    session_factory = make_session_factory(engine)
    provider = OpenFootballProvider()
    now = datetime.now(UTC)
    run_id = None
    source_id = None
    competition_id = None
    try:
        with session_factory() as session:
            competition = session.scalar(
                select(Competition).where(Competition.code == args.competition)
            )
            source = session.scalar(
                select(DataSource).where(DataSource.code == provider.source_code)
            )
            mapping = (
                session.scalar(
                    select(SourceCompetition).where(
                        SourceCompetition.competition_id == competition.id,
                        SourceCompetition.source_id == source.id,
                    )
                )
                if competition is not None and source is not None
                else None
            )
            if mapping is None or not mapping.external_code:
                raise EntityResolutionError(
                    "Falta el mapping OpenFootball; ejecuta primero "
                    "scripts/bootstrap.py --catalog-only"
                )
            dataset_file = mapping.external_code
            source_id = source.id
            competition_id = competition.id

        with session_factory.begin() as session:
            run = SyncRun(
                mode=SyncMode.MANUAL.value,
                status=SyncStatus.RUNNING.value,
                started_at=now,
            )
            session.add(run)
            session.flush()
            run_id = run.id

        teams = list(
            provider.get_teams(
                args.competition,
                args.season,
                dataset_file,
                timezone_name=COMPETITION_TIMEZONES[args.competition],
            )
        )
        matches = list(
            provider.get_matches(
                args.competition,
                args.season,
                dataset_file,
                timezone_name=COMPETITION_TIMEZONES[args.competition],
            )
        )
        season_label = f"{args.season}-{(args.season + 1) % 100:02d}"
        season = NormalizedSeason(
            source_code=provider.source_code,
            competition_code=args.competition,
            external_id=None,
            external_code=dataset_file,
            external_season=season_label,
            start_year=args.season,
            end_year=args.season + 1,
        )

        inserted = updated = skipped = conflicts = 0
        with session_factory.begin() as session:
            source = session.scalar(
                select(DataSource).where(DataSource.code == provider.source_code)
            )
            if source is None:
                raise EntityResolutionError("Falta la fuente OpenFootball en el catálogo")
            competition_season = upsert_competition_season(session, season)
            team_ids = {team.external_id: upsert_team(session, team).id for team in teams}
            for match in matches:
                home_id = team_ids[match.home_external_id]
                away_id = team_ids[match.away_external_id]
                result = upsert_match(
                    session,
                    match,
                    competition_season_id=competition_season.id,
                    home_team_id=home_id,
                    away_team_id=away_id,
                )
                inserted += int(result.inserted)
                updated += int(not result.inserted and bool(result.updated_fields))
                skipped += int(not result.inserted and not result.updated_fields)
                conflicts += len(result.conflict_fields)
            competition_season.last_synced_at = now

        requests_count = provider.request_count
        with session_factory.begin() as session:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="OPENFOOTBALL_HISTORICAL_IMPORT",
                    status=SyncStatus.SUCCESS.value,
                    requests_count=requests_count,
                    inserted_count=inserted,
                    updated_count=updated,
                    skipped_count=skipped,
                )
            )
            run = session.get(SyncRun, run_id)
            run.status = SyncStatus.SUCCESS.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = requests_count
            run.inserted_count = inserted
            run.updated_count = updated
            run.skipped_count = skipped
            run.conflict_count = conflicts
            run.summary = f"OpenFootball {args.competition} {season_label} importado"

        print(f"OpenFootball: {args.competition} {season_label}")
        print(f"Partidos leídos: {len(matches)}; equipos: {len(teams)}")
        print(
            f"Nuevos: {inserted}; actualizados: {updated}; "
            f"sin cambios: {skipped}; conflictos: {conflicts}"
        )
        print(f"Solicitudes HTTP: {provider.request_count}")
        return 0
    except Exception as error:
        if run_id is not None and source_id is not None:
            try:
                with session_factory.begin() as session:
                    session.add(
                        SyncRunItem(
                            sync_run_id=run_id,
                            source_id=source_id,
                            competition_id=competition_id,
                            operation="OPENFOOTBALL_HISTORICAL_IMPORT",
                            status=SyncStatus.FAILED.value,
                            requests_count=provider.request_count,
                            error_count=1,
                            error_code=error.__class__.__name__,
                            message=str(error)[:1000],
                        )
                    )
                    run = session.get(SyncRun, run_id)
                    if run is not None:
                        run.status = SyncStatus.FAILED.value
                        run.finished_at = datetime.now(UTC)
                        run.requests_count = provider.request_count
                        run.error_count = 1
                        run.summary = f"{error.__class__.__name__}: {str(error)[:900]}"
            except Exception as log_error:
                error_name = log_error.__class__.__name__
                print(f"No se pudo registrar el fallo de sincronización: {error_name}")
        print(f"Importación OpenFootball fallida: {error.__class__.__name__}: {error}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
