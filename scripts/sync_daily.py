"""Daily sync and Sunday season reconciliation entry point."""

import argparse
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from football_analytics.config.constants import COMPETITIONS
from football_analytics.config.settings import get_settings
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.enums import SyncMode
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.sync.orchestrator import run_sync


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(definition.code for definition in COMPETITIONS),
        help="Limit sync to selected competition codes, e.g. --competitions UEL.",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.sync_enabled:
        print("Sincronización deshabilitada por SYNC_ENABLED.")
        return 0
    engine = make_engine()
    try:
        factory = make_session_factory(engine)
        providers = {
            "football-data": FootballDataProvider(),
            "openligadb": OpenLigaDBProvider(),
        }
        selected = tuple(args.competitions) if args.competitions else None
        summary = run_sync(factory, providers, SyncMode.DAILY, competition_codes=selected)
        print(f"Daily sync                 {summary.status.value}")
        print(f"Inserted: {summary.inserted_count}")
        print(f"Updated: {summary.updated_count}")
        print(f"Skipped: {summary.skipped_count}")
        print(f"Conflicts: {summary.conflict_count}")
        print(f"Errors: {summary.error_count}")

        local_now = datetime.now(UTC).astimezone(ZoneInfo(settings.app_timezone))
        if local_now.weekday() == 6:
            reconciliation = run_sync(
                factory, providers, SyncMode.RECONCILIATION, competition_codes=selected
            )
            print(f"Weekly reconciliation     {reconciliation.status.value}")
            if reconciliation.error_count:
                return 2
        return 0 if summary.error_count == 0 else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
