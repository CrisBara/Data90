"""Create/update local schema and seed source and competition metadata."""

import argparse

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from football_analytics.config.constants import COMPETITIONS, SOURCES
from football_analytics.database.models import Competition, DataSource, SourceCompetition
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.enums import SyncMode
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.services.api_football_player_sync import sync_api_football_players
from football_analytics.services.eredivisie_fantasy_sync import sync_eredivisie_fantasy_players
from football_analytics.services.football_data_squad_sync import sync_current_football_data_squads
from football_analytics.services.match_event_sync import sync_match_goal_events
from football_analytics.services.player_sync import sync_player_statistics
from football_analytics.services.promiedos_sync import sync_promiedos_player_statistics
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
    config = Config("alembic.ini")
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
        print(f"Sources seeded             {len(SOURCES)}")
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
        player_summary = sync_player_statistics(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
            mode=SyncMode.BOOTSTRAP,
        )
        print(f"Player statistics          {player_summary.status.value}")
        print(f"Player rows inserted       {player_summary.inserted_count}")
        print(f"Player rows updated        {player_summary.updated_count}")
        print(f"Player sync errors         {player_summary.error_count}")
        event_summary = sync_match_goal_events(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"Goal events                {event_summary.status.value}")
        print(f"Goal events inserted       {event_summary.inserted_count}")
        print(f"Goal event sync errors     {event_summary.error_count}")
        api_players = sync_api_football_players(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"API-Football players       {api_players.status.value}")
        print(
            f"Player profiles/stats      {api_players.inserted_count} new · "
            f"{api_players.updated_count} updated"
        )
        print(f"API-Football errors        {api_players.error_count}")
        promiedos = sync_promiedos_player_statistics(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"Promiedos current stats    {promiedos.status.value}")
        print(f"Current stats inserted     {promiedos.inserted_count}")
        print(f"Current stats updated      {promiedos.updated_count}")
        print(f"Promiedos errors           {promiedos.error_count}")
        eredivisie = (
            sync_eredivisie_fantasy_players(
                make_session_factory(engine),
                # Restrict this extra player API call to the selected season scope when supplied.
            )
            if not args.competitions or "EREDIVISIE" in args.competitions
            else None
        )
        squads = sync_current_football_data_squads(
            make_session_factory(engine),
            provider=FootballDataProvider(),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"Current full squads        {squads['status'].value}")
        print(f"Current squad clubs        {squads['teams_count']}")
        print(f"Squad profiles inserted    {squads['inserted_count']}")
        print(f"Squad profiles updated     {squads['updated_count']}")
        print(f"Squad sync errors          {squads['error_count']}")
        return (
            0
            if summary.error_count == 0
            and player_summary.error_count == 0
            and event_summary.error_count == 0
            and api_players.error_count == 0
            and promiedos.error_count == 0
            and (eredivisie is None or eredivisie["error_count"] == 0)
            and squads["error_count"] == 0
            else 2
        )
    except Exception as error:
        print(f"Bootstrap failed: {error.__class__.__name__}: {error}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
