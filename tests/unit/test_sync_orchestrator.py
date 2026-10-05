from datetime import UTC, date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from football_analytics.config.constants import COMPETITIONS, SOURCES
from football_analytics.database.base import Base
from football_analytics.database.models import Competition, DataSource, SyncRunItem
from football_analytics.domain.dto import NormalizedSeason
from football_analytics.domain.enums import SyncMode, SyncStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.football_data import FootballDataProvider
from football_analytics.providers.openligadb import OpenLigaDBProvider
from football_analytics.sync.orchestrator import run_sync


class PartialFootballProvider(FootballDataProvider):
    def __init__(self) -> None:
        super().__init__(token="fixture")

    def get_current_season(self, competition_code: str) -> NormalizedSeason:
        if competition_code == "PL":
            raise ProviderError("fixture failure")
        return NormalizedSeason(
            source_code=self.source_code,
            competition_code=competition_code,
            external_id=f"season-{competition_code}",
            external_code=competition_code,
            external_season="2026",
            start_year=2026,
            end_year=2027,
            start_date=date(2026, 8, 1),
            end_date=date(2027, 5, 31),
        )

    def get_teams(self, competition_code: str, season_start_year: int) -> list:
        return []

    def get_matches(self, *args: object) -> list:
        return []

    def get_standings(self, competition_code: str, season_start_year: int) -> list:
        return []


class FixtureOpenLigaDBProvider(OpenLigaDBProvider):
    def validate_league_shortcut(self, competition_names: tuple[str, ...], season: int) -> str:
        return "fixture-uel"

    def get_teams(self, shortcut: str, season: int) -> list:
        return []

    def get_matches(self, *args: object) -> list:
        return []

    def get_changed_matches(self, *args: object) -> tuple[list, dict, int]:
        return [], {}, 0

    def get_standings(self, *args: object) -> list:
        return []


def test_one_competition_failure_does_not_stop_other_competitions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all(
            [
                DataSource(
                    id=index,
                    code=source["code"],
                    name=source["name"],
                    priority=source["priority"],
                    requires_api_key=source["requires_api_key"],
                )
                for index, source in enumerate(SOURCES, start=1)
            ]
        )
        session.add_all(
            [
                Competition(
                    id=index,
                    code=competition.code,
                    name=competition.name,
                    display_order=competition.display_order,
                )
                for index, competition in enumerate(COMPETITIONS, start=1)
            ]
        )

    summary = run_sync(
        factory,
        {
            "football-data": PartialFootballProvider(),
            "openligadb": FixtureOpenLigaDBProvider(),
        },
        SyncMode.DAILY,
        now=datetime(2026, 10, 5, 12, tzinfo=UTC),
    )

    with factory() as session:
        items = session.scalars(select(SyncRunItem)).all()
    assert summary.status == SyncStatus.PARTIAL
    assert summary.error_count == 1
    assert len(items) == 8
    assert sum(item.status == "SUCCESS" for item in items) == 7
    assert sum(item.status == "ERROR" for item in items) == 1
    engine.dispose()


def test_sync_can_retry_only_the_selected_competition() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all(
            [
                DataSource(
                    id=index,
                    code=source["code"],
                    name=source["name"],
                    priority=source["priority"],
                    requires_api_key=source["requires_api_key"],
                )
                for index, source in enumerate(SOURCES, start=1)
            ]
        )
        session.add_all(
            [Competition(code=definition.code, name=definition.name) for definition in COMPETITIONS]
        )

    summary = run_sync(
        factory,
        {"openligadb": FixtureOpenLigaDBProvider()},
        SyncMode.BOOTSTRAP,
        now=datetime(2026, 10, 5, 12, tzinfo=UTC),
        competition_codes=("UEL",),
    )

    with factory() as session:
        items = session.scalars(select(SyncRunItem)).all()
    assert summary.status == SyncStatus.SUCCESS
    assert len(items) == 1
    assert items[0].competition_id == 8
    engine.dispose()
