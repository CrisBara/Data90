"""Initial backfill of current-season match statistics for supported competitions."""

from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.providers.espn import ESPNProvider
from football_analytics.services.espn_match_statistics_sync import sync_espn_match_statistics


def main() -> int:
    engine = make_engine()
    try:
        summary = sync_espn_match_statistics(make_session_factory(engine), provider=ESPNProvider())
        print(summary.message)
        print(f"Solicitudes: {summary.requests_count}; estado: {summary.status}")
        return 0 if summary.error_count == 0 else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
