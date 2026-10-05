"""Application entry points for provider-backed synchronization."""

from football_analytics.database.session import make_engine, make_session_factory
from football_analytics.domain.enums import SyncMode
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.sync.orchestrator import SyncSummary, run_sync


def run_daily_sync() -> SyncSummary:
    engine = make_engine()
    try:
        return run_sync(
            make_session_factory(engine),
            {
                "football-data": FootballDataProvider(),
                "openligadb": OpenLigaDBProvider(),
            },
            SyncMode.DAILY,
        )
    finally:
        engine.dispose()
