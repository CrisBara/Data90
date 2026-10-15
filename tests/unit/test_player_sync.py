from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from data90.database.base import Base
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    PlayerCompetitionSeasonStatistic,
    Season,
    SourceCompetitionSeason,
    SyncRunItem,
)
from data90.domain.dto import NormalizedPlayerSeasonStatistic
from data90.domain.enums import SyncStatus
from data90.providers.openligadb import OpenLigaDBProvider
from data90.services.player_sync import sync_player_statistics


class GoalGetterFixture(OpenLigaDBProvider):
    def __init__(self) -> None:
        super().__init__()
        self.request_count = 2

    def get_available_leagues(self, season: int) -> list[dict[str, str]]:
        assert season == 2026
        return [
            {
                "leagueName": "Premier League 2026/2027",
                "leagueShortcut": "pl",
                "leagueSeason": "2026",
            }
        ]

    def get_goal_getters(self, competition_code: str, season_start_year: int, shortcut: str):
        assert (competition_code, season_start_year, shortcut) == ("PL", 2026, "pl")
        return [
            NormalizedPlayerSeasonStatistic(
                source_code="openligadb",
                external_id="42",
                display_name="Forward Example",
                competition_code="PL",
                season_start_year=2026,
                goals=7,
            )
        ]


def test_player_sync_persists_scorers_and_provider_mapping() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        source = DataSource(
            id=1, code="openligadb", name="OpenLigaDB", priority=2, requires_api_key=False
        )
        competition = Competition(id=1, code="PL", name="Premier League")
        season = Season(id=1, label="2026/27", start_year=2026, end_year=2027, active=True)
        session.add_all((source, competition, season))
        session.flush()
        session.add(
            CompetitionSeason(
                id=1, competition_id=competition.id, season_id=season.id, status="ACTIVE"
            )
        )

    summary = sync_player_statistics(factory, GoalGetterFixture(), competition_codes=("PL",))

    assert summary.status == SyncStatus.SUCCESS
    assert summary.inserted_count == 1
    assert summary.requests_count == 2
    with Session(engine) as session:
        row = session.scalar(select(PlayerCompetitionSeasonStatistic))
        mapping = session.scalar(select(SourceCompetitionSeason))
        item = session.scalar(select(SyncRunItem))
        assert row is not None and row.goals == 7
        assert mapping is not None and mapping.external_code == "pl"
        assert item is not None and item.status == SyncStatus.SUCCESS.value
    engine.dispose()
