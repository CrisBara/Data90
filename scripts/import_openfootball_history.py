"""Bulk import available historical seasons for OpenFootball domestic leagues."""

import argparse
from datetime import UTC, datetime

from sqlalchemy import select

from football_analytics.database.models import (
    Competition,
    DataSource,
    Season,
    SourceCompetition,
    SyncRun,
    SyncRunItem,
)
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.dto import NormalizedSeason
from football_analytics.domain.enums import SyncMode, SyncStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.openfootball import (
    COMPETITION_TIMEZONES,
    OpenFootballProvider,
)
from football_analytics.repositories.catalog import upsert_competition_season, upsert_team
from football_analytics.repositories.match_sync import upsert_match


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-year", type=int, default=2010, help="First season start year")
    parser.add_argument(
        "--to-year", type=int, default=datetime.now(UTC).year - 1, help="Last start year"
    )
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(COMPETITION_TIMEZONES),
        default=list(COMPETITION_TIMEZONES),
    )
    args = parser.parse_args()
    if args.from_year > args.to_year:
        parser.error("--from-year debe ser menor o igual que --to-year")

    engine = make_engine()
    factory = make_session_factory(engine)
    provider = OpenFootballProvider()
    now = datetime.now(UTC)
    try:
        with factory.begin() as session:
            source = session.scalar(select(DataSource).where(DataSource.code == "openfootball"))
            if source is None:
                parser.error("Falta la fuente OpenFootball. Ejecuta bootstrap --catalog-only.")
            source_id = source.id
            catalog = {
                competition.code: (
                    competition.id,
                    session.scalar(
                        select(SourceCompetition).where(
                            SourceCompetition.competition_id == competition.id,
                            SourceCompetition.source_id == source_id,
                        )
                    ),
                )
                for competition in session.scalars(
                    select(Competition).where(Competition.code.in_(args.competitions))
                )
            }
            run = SyncRun(
                mode=SyncMode.MANUAL.value,
                status=SyncStatus.RUNNING.value,
                started_at=now,
                summary="Importación histórica OpenFootball en curso",
            )
            session.add(run)
            session.flush()
            run_id = run.id

        inserted_total = updated_total = skipped_total = conflict_total = error_total = 0
        success_count = skipped_seasons = 0
        for code in args.competitions:
            competition_id, mapping = catalog.get(code, (None, None))
            dataset_file = mapping.external_code if mapping is not None else None
            for year in range(args.from_year, args.to_year + 1):
                requests_before = provider.request_count
                inserted = updated = skipped = conflicts = 0
                status = SyncStatus.SUCCESS.value
                message = None
                try:
                    if competition_id is None or not dataset_file:
                        raise ValueError(f"No hay dataset configurado para {code}")
                    matches = list(
                        provider.get_matches(
                            code,
                            year,
                            dataset_file,
                            timezone_name=COMPETITION_TIMEZONES[code],
                            skip_incomplete=True,
                        )
                    )
                    teams = list(
                        provider.get_teams(
                            code,
                            year,
                            dataset_file,
                            timezone_name=COMPETITION_TIMEZONES[code],
                            skip_incomplete=True,
                        )
                    )
                    label = f"{year}-{(year + 1) % 100:02d}"
                    normalized_season = NormalizedSeason(
                        source_code=provider.source_code,
                        competition_code=code,
                        external_id=None,
                        external_code=dataset_file,
                        external_season=label,
                        start_year=year,
                        end_year=year + 1,
                    )
                    with factory.begin() as session:
                        competition_season = upsert_competition_season(session, normalized_season)
                        season = session.get(Season, competition_season.season_id)
                        if season is not None:
                            season.active = year >= datetime.now(UTC).year
                        team_ids = {
                            team.external_id: upsert_team(session, team).id for team in teams
                        }
                        for match in matches:
                            result = upsert_match(
                                session,
                                match,
                                competition_season_id=competition_season.id,
                                home_team_id=team_ids[match.home_external_id],
                                away_team_id=team_ids[match.away_external_id],
                            )
                            inserted += int(result.inserted)
                            updated += int(not result.inserted and bool(result.updated_fields))
                            skipped += int(not result.inserted and not result.updated_fields)
                            conflicts += len(result.conflict_fields)
                        competition_season.status = (
                            "ACTIVE" if year >= datetime.now(UTC).year else "COMPLETED"
                        )
                        competition_season.last_synced_at = now
                    skipped += provider.incomplete_match_count
                    if not matches:
                        status = "SKIPPED"
                        message = "Dataset disponible sin partidos para este período."
                        skipped_seasons += 1
                    else:
                        success_count += 1
                except ProviderError as error:
                    if getattr(error, "status_code", None) == 404:
                        status = "SKIPPED"
                        message = "El dataset de esta temporada no está publicado."
                        skipped_seasons += 1
                    else:
                        status = SyncStatus.FAILED.value
                        message = f"{error.__class__.__name__}: {str(error)[:850]}"
                        error_total += 1
                except Exception as error:
                    status = SyncStatus.FAILED.value
                    message = f"{error.__class__.__name__}: {str(error)[:850]}"
                    error_total += 1

                inserted_total += inserted
                updated_total += updated
                skipped_total += skipped
                conflict_total += conflicts
                with factory.begin() as session:
                    session.add(
                        SyncRunItem(
                            sync_run_id=run_id,
                            source_id=source_id,
                            competition_id=competition_id,
                            operation="OPENFOOTBALL_HISTORICAL_IMPORT",
                            status=status,
                            requests_count=provider.request_count - requests_before,
                            inserted_count=inserted,
                            updated_count=updated,
                            skipped_count=skipped,
                            error_count=int(status == SyncStatus.FAILED.value),
                            error_code="IMPORT_ERROR"
                            if status == SyncStatus.FAILED.value
                            else None,
                            message=f"{code} {year}-{(year + 1) % 100:02d}: {message}"
                            if message
                            else f"{code} {year}-{(year + 1) % 100:02d}",
                        )
                    )
                print(
                    f"{code} {year}-{(year + 1) % 100:02d}: {status}; "
                    f"{inserted} nuevos, {updated} actualizados, {skipped} omitidos/sin cambios"
                )

        status = (
            SyncStatus.FAILED
            if error_total and success_count == 0
            else SyncStatus.PARTIAL
            if error_total
            else SyncStatus.SUCCESS
        )
        with factory.begin() as session:
            run = session.get(SyncRun, run_id)
            run.status = status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = provider.request_count
            run.inserted_count = inserted_total
            run.updated_count = updated_total
            run.skipped_count = skipped_total
            run.conflict_count = conflict_total
            run.error_count = error_total
            run.summary = (
                f"OpenFootball: {success_count} temporadas importadas, "
                f"{skipped_seasons} ausentes/vacías, {error_total} errores"
            )
        print(run.summary)
        print(f"Solicitudes HTTP: {provider.request_count}")
        return 2 if error_total else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
