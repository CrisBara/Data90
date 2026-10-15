"""Import 2026/27 match and scorer statistics from PitchAPI."""

import argparse

from football_analytics.config.constants import COMPETITIONS
from football_analytics.config.settings import get_settings
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.services.pitchapi_match_sync import sync_pitchapi_match_statistics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(definition.code for definition in COMPETITIONS),
        help="Restrict the import to selected target leagues.",
    )
    args = parser.parse_args()
    if not get_settings().pitchapi_api_key.get_secret_value():
        parser.error("Configura PITCHAPI_API_KEY en .env antes de iniciar la importación.")

    engine = make_engine()
    try:
        summary = sync_pitchapi_match_statistics(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"PitchAPI statistics: {summary.status.value}")
        print(f"Requests: {summary.requests_count}")
        print(f"Rows inserted: {summary.inserted_count}")
        print(f"Rows updated: {summary.updated_count}")
        print(f"Skipped: {summary.skipped_count}")
        print(f"Errors: {summary.error_count}")
        print(summary.message)
        return 0 if summary.error_count == 0 else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
