from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data90.database.base import Base
from data90.database.models import Competition, CompetitionSeason, Match, Season, Team
from data90.services.match_service import MatchService


def test_match_service_filters_by_local_date_and_competition() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Competition(id=1, code="PL", name="Premier League", display_order=1),
                Season(id=1, label="2026/27", start_year=2026, end_year=2027),
                CompetitionSeason(id=1, competition_id=1, season_id=1, status="ACTIVE"),
                Team(id=1, canonical_name="Manchester City"),
                Team(id=2, canonical_name="Arsenal"),
                Team(id=3, canonical_name="Chelsea"),
                Match(
                    id=1,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=2,
                    kickoff_utc=datetime(2026, 10, 5, 2, tzinfo=UTC),
                    status="SCHEDULED",
                ),
                Match(
                    id=2,
                    competition_season_id=1,
                    home_team_id=2,
                    away_team_id=3,
                    kickoff_utc=datetime(2026, 10, 5, 3, tzinfo=UTC),
                    status="SCHEDULED",
                ),
            ]
        )
        session.commit()

        previous_local_day = MatchService(session).get_matches(date(2026, 10, 4))
        current_local_day = MatchService(session).get_matches(date(2026, 10, 5), ("PL",))
        no_results = MatchService(session).get_matches(date(2026, 10, 5), ())
    engine.dispose()

    assert [match.match_id for match in previous_local_day] == [1]
    assert [match.match_id for match in current_local_day] == [2]
    assert no_results == []
    assert current_local_day[0].home_team == "Arsenal"
    assert current_local_day[0].away_team == "Chelsea"
