"""Create/update local schema and seed source and competition metadata."""

import argparse

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from football_analytics.config.constants import COMPETITIONS, SOURCES
from football_analytics.config.paths import alembic_resource_root
from football_analytics.database.models import Competition, DataSource, SourceCompetition
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.enums import SyncMode
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.sync.orchestrator import run_sync


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Apply migrations and seed local catalogs without calling provider APIs.",
    )
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(definition.code for definition in COMPETITIONS),
        help="Limit provider bootstrap to selected competition codes, e.g. --competitions UEL.",
    )
    args = parser.parse_args()
    engine = make_engine()
    migration_root = alembic_resource_root()
    config = Config(str(migration_root / "alembic.ini"))
    config.set_main_option("script_location", str(migration_root / "alembic"))
    print("Football Analytics Pro Bootstrap\n")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        print("PostgreSQL                OK")
        command.upgrade(config, "head")
        session_factory = make_session_factory(engine)
        with session_factory.begin() as session:
            sources = {item.code: item for item in session.scalars(select(DataSource)).all()}
            for item in SOURCES:
                model = sources.get(item["code"]) or DataSource(
                    code=item["code"],
                    name=item["name"],
                    priority=item["priority"],
                    requires_api_key=item["requires_api_key"],
                )
                for field, value in item.items():
                    setattr(model, field, value)
                session.add(model)
            competitions = {item.code: item for item in session.scalars(select(Competition)).all()}
            for item in COMPETITIONS:
                model = competitions.get(item.code) or Competition(code=item.code, name=item.name)
                model.name = item.name
                model.country_name = item.country_name
                model.region_name = item.region_name
                model.competition_type = item.competition_type
                model.display_order = item.display_order
                model.active = True
                session.add(model)
            session.flush()
            sources_by_code = {
                item.code: item for item in session.scalars(select(DataSource)).all()
            }
            competitions_by_code = {
                item.code: item for item in session.scalars(select(Competition)).all()
            }
            for definition in COMPETITIONS:
                for source_code, external_code in definition.provider_codes:
                    source = sources_by_code[source_code]
                    competition = competitions_by_code[definition.code]
                    mapping = session.scalar(
                        select(SourceCompetition).where(
                            SourceCompetition.source_id == source.id,
                            SourceCompetition.competition_id == competition.id,
                        )
                    )
                    if mapping is None:
                        mapping = SourceCompetition(
                            source_id=source.id, competition_id=competition.id
                        )
                    mapping.external_code = external_code or mapping.external_code
                    session.add(mapping)
        print("Competitions seeded        8")
        print("Sources seeded             3")
        mapping_count = sum(len(definition.provider_codes) for definition in COMPETITIONS)
        print(f"Source mappings seeded     {mapping_count}")
        print("Schema migration            OK")
        if args.catalog_only:
            print("Catalog-only bootstrap     complete (no external requests)")
            return 0
        summary = run_sync(
            make_session_factory(engine),
            {
                "football-data": FootballDataProvider(),
                "openligadb": OpenLigaDBProvider(),
            },
            SyncMode.BOOTSTRAP,
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"Provider bootstrap         {summary.status.value}")
        print(f"Teams and matches          {summary.inserted_count} inserted")
        print(f"Matches skipped             {summary.skipped_count}")
        print(f"Conflicts                  {summary.conflict_count}")
        print(summary.message)
        return 0 if summary.status.value == "SUCCESS" else 2
    except Exception as error:
        print(f"Bootstrap failed: {error.__class__.__name__}: {error}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
