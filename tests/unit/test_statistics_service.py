from datetime import UTC, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from data90.database.base import Base
from data90.database.models import (
    Competition,
    CompetitionSeason,
    Match,
    Season,
    Team,
)
from data90.services.statistics_service import StatisticsService


def test_competition_statistics_use_a_fixed_number_of_queries_and_skip_unknown_scores() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Competition(id=1, code="PL", name="Premier League"),
                Season(id=1, label="2026/27", start_year=2026, end_year=2027),
                CompetitionSeason(id=1, competition_id=1, season_id=1, status="ACTIVE"),
                Team(id=1, canonical_name="Home FC"),
                Team(id=2, canonical_name="Away FC"),
                Team(id=3, canonical_name="Future FC"),
                Match(
                    id=1,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=2,
                    kickoff_utc=datetime(2026, 8, 1, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=2,
                    away_score_ft=1,
                ),
                Match(
                    id=2,
                    competition_season_id=1,
                    home_team_id=2,
                    away_team_id=3,
                    kickoff_utc=datetime(2026, 8, 8, tzinfo=UTC),
                    status="FINISHED",
                ),
                Match(
                    id=3,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=3,
                    kickoff_utc=datetime(2026, 8, 15, tzinfo=UTC),
                    status="SCHEDULED",
                ),
            ]
        )
        session.commit()

        select_count = 0

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            statistics = StatisticsService(session).for_competition_season(1)
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

    by_id = {item.team_id: item for item in statistics}
    assert select_count == 3
    assert set(by_id) == {1, 2, 3}
    assert (by_id[1].metrics.played, by_id[1].metrics.won, by_id[1].metrics.points) == (1, 1, 3)
    assert (by_id[2].metrics.played, by_id[2].metrics.lost, by_id[2].metrics.points) == (1, 1, 0)
    assert by_id[3].metrics.played == 0
    engine.dispose()
