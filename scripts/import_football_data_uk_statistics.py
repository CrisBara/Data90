"""Import current-season team match statistics from free public CSV files."""

from data90.config.constants import COMPETITIONS
from data90.database.session import make_engine, make_session_factory
from data90.services.football_data_uk_sync import sync_football_data_uk_statistics


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(item.code for item in COMPETITIONS),
        help="Limit the import to selected league competitions.",
    )
    args = parser.parse_args()
    engine = make_engine()
    try:
        summary = sync_football_data_uk_statistics(
            make_session_factory(engine),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(f"Football-Data.co.uk statistics: {summary.status.value}")
        print(f"Requests: {summary.requests_count}")
        print(f"Rows inserted: {summary.inserted_count}")
        print(f"Rows updated: {summary.updated_count}")
        print(f"Skipped: {summary.skipped_count}")
        print(f"Errors: {summary.error_count}")
        if summary.message:
            print(summary.message)
        return 0 if summary.error_count == 0 else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
