"""Import one resumable, quota-limited page of Highlightly player match statistics."""

import argparse

from football_analytics.config.constants import COMPETITIONS
from football_analytics.config.settings import get_settings
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.services.highlightly_match_sync import sync_highlightly_match_statistics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(definition.code for definition in COMPETITIONS),
        help="Restrict the resumable import to selected competitions.",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.highlightly_api_key.get_secret_value():
        parser.error("Configura HIGHLIGHTLY_API_KEY en .env antes de iniciar la importación.")
    engine = make_engine()
    try:
        summary = sync_highlightly_match_statistics(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"Highlightly player match statistics: {summary.status.value}")
        print(f"Requests: {summary.requests_count}")
        print(f"Player match rows inserted: {summary.inserted_count}")
        print(f"Player match rows updated: {summary.updated_count}")
        print(f"Skipped: {summary.skipped_count}")
        print(f"Errors: {summary.error_count}")
        print(summary.message)
        return 0 if summary.error_count == 0 else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
