"""Import full current-season fixtures and results from Promiedos."""

import argparse

from football_analytics.config.constants import COMPETITIONS
from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.providers.promiedos import PromiedosProvider
from football_analytics.services.promiedos_match_sync import sync_promiedos_match_fixtures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        choices=tuple(item.code for item in COMPETITIONS),
        help="Only import selected competitions.",
    )
    args = parser.parse_args()
    engine = make_engine()
    try:
        summary = sync_promiedos_match_fixtures(
            make_session_factory(engine),
            provider=PromiedosProvider(),
            competition_codes=tuple(args.competitions) if args.competitions else None,
        )
        print(summary.message)
        print(f"Solicitudes: {summary.requests_count}; estado: {summary.status.value}")
        return 0 if summary.error_count == 0 else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
