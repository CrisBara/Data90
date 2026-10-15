"""Import current individual goal events that match stored fixtures unambiguously."""

import argparse

from data90.database.session import make_engine, make_session_factory
from data90.services.match_event_sync import sync_match_goal_events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season-year", type=int, default=None)
    args = parser.parse_args()
    engine = make_engine()
    try:
        summary = sync_match_goal_events(make_session_factory(engine), season_year=args.season_year)
        print(f"OpenLigaDB goal events: {summary.status.value}")
        print(summary.message)
        return 2 if summary.error_count else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
