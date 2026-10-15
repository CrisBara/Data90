from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from data90.database.base import Base
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchEvent,
    Season,
    SourceCompetitionSeason,
    Team,
)
from data90.domain.enums import SyncStatus
from data90.providers.openligadb import OpenLigaDBProvider
from data90.services.match_event_sync import sync_match_goal_events


class GoalEventFixture(OpenLigaDBProvider):
    def __init__(self) -> None:
        super().__init__()
        self.request_count = 1

    def get_match_data(self, shortcut: str, season_start_year: int) -> list[dict]:
        assert (shortcut, season_start_year) == ("pl", 2026)
        return [
            {
                "matchID": 9001,
                "matchDateTimeUTC": "2026-08-01T16:00:00Z",
                "matchIsFinished": True,
                "team1": {"teamId": 100, "teamName": "Arsenal"},
                "team2": {"teamId": 200, "teamName": "Chelsea"},
                "goals": [
                    {
                        "goalID": 1,
                        "goalGetterID": 42,
                        "goalGetterName": "Forward Example",
                        "scoringTeamId": 100,
                        "matchMinute": 24,
                        "isPenalty": False,
                        "isOwnGoal": False,
                    }
                ],
            }
        ]


def test_goal_event_sync_links_and_deduplicates_individual_goals() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        source = DataSource(
            id=1, code="openligadb", name="OpenLigaDB", priority=2, requires_api_key=False
        )
        competition = Competition(id=1, code="PL", name="Premier League")
        season = Season(id=1, label="2026/27", start_year=2026, end_year=2027, active=True)
        home = Team(id=1, canonical_name="Arsenal")
        away = Team(id=2, canonical_name="Chelsea")
        session.add_all((source, competition, season, home, away))
        session.flush()
        competition_season = CompetitionSeason(
            id=1, competition_id=competition.id, season_id=season.id, status="ACTIVE"
        )
        session.add(competition_season)
        session.flush()
        session.add(
            SourceCompetitionSeason(
                source_id=source.id,
                competition_season_id=competition_season.id,
                external_code="pl",
                external_season="2026",
            )
        )
        session.add(
            Match(
                id=1,
                competition_season_id=competition_season.id,
                home_team_id=home.id,
                away_team_id=away.id,
                kickoff_utc=datetime(2026, 8, 1, 16, 0, tzinfo=UTC),
                status="FINISHED",
            )
        )

    first = sync_match_goal_events(factory, GoalEventFixture(), season_year=2026)
    second = sync_match_goal_events(factory, GoalEventFixture(), season_year=2026)

    assert first.error_count == 0, first.message
    assert first.status == SyncStatus.SUCCESS
    assert first.inserted_count == 1
    assert second.inserted_count == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MatchEvent)) == 1
        event = session.scalar(select(MatchEvent))
        assert event is not None
        assert (event.event_type, event.minute, event.player_name) == (
            "GOAL",
            24,
            "Forward Example",
        )
        assert event.player_id is not None
    engine.dispose()
