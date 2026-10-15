from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from football_analytics.database.base import Base
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Season,
    SourceTeam,
    SyncRunItem,
    Team,
)
from football_analytics.services.football_data_squad_sync import (
    sync_current_football_data_squads,
)


class EmptySquadProvider:
    source_code = "football-data"
    request_count = 0

    def get_team_details(self, external_id: str) -> dict:
        assert external_id == "team-1"
        self.request_count += 1
        return {"squad": [], "coach": None}


def test_empty_published_squad_is_a_skip_not_a_sync_failure() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        source = DataSource(
            id=1, code="football-data", name="football-data.org", priority=1,
            requires_api_key=True,
        )
        competition = Competition(id=1, code="PL", name="Premier League")
        season = Season(id=1, label="2026/27", start_year=2026, end_year=2027, active=True)
        team = Team(id=1, canonical_name="Example FC")
        away = Team(id=2, canonical_name="Away FC")
        session.add_all((source, competition, season, team, away))
        session.flush()
        competition_season = CompetitionSeason(
            id=1, competition_id=competition.id, season_id=season.id, status="ACTIVE"
        )
        session.add_all(
            (
                competition_season,
                SourceTeam(source_id=source.id, team_id=team.id, external_id="team-1"),
                Match(
                    competition_season_id=competition_season.id,
                    home_team_id=team.id,
                    away_team_id=away.id,
                    kickoff_utc=datetime(2026, 8, 1, 16, 0, tzinfo=UTC),
                    status="SCHEDULED",
                ),
            )
        )

    result = sync_current_football_data_squads(factory, EmptySquadProvider())

    assert result["error_count"] == 0
    assert result["skipped_count"] == 1
    with Session(engine) as session:
        item = session.scalar(select(SyncRunItem))
        assert item is not None
        assert item.status == "SKIPPED"
        assert item.error_count == 0
        assert "no publicó el plantel" in item.message
    engine.dispose()
