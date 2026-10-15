from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from football_analytics.database.base import Base
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    Match,
    Season,
    Team,
)
from football_analytics.services.team_service import TeamService


def test_team_metrics_use_only_finished_matches_with_known_scores() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Competition(id=1, code="PL", name="Premier League"),
                Season(id=1, label="2026/27", start_year=2026, end_year=2027),
                CompetitionSeason(id=1, competition_id=1, season_id=1, status="ACTIVE"),
                Team(id=1, canonical_name="Example FC"),
                Team(id=2, canonical_name="Opponent FC"),
                Match(
                    id=1,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=2,
                    kickoff_utc=datetime(2026, 8, 1, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=2,
                    away_score_ft=0,
                ),
                Match(
                    id=2,
                    competition_season_id=1,
                    home_team_id=2,
                    away_team_id=1,
                    kickoff_utc=datetime(2026, 8, 8, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=1,
                    away_score_ft=1,
                ),
                Match(
                    id=3,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=2,
                    kickoff_utc=datetime(2026, 8, 15, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=0,
                    away_score_ft=1,
                ),
                Match(
                    id=4,
                    competition_season_id=1,
                    home_team_id=2,
                    away_team_id=1,
                    kickoff_utc=datetime(2026, 8, 22, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=0,
                    away_score_ft=3,
                ),
                Match(
                    id=5,
                    competition_season_id=1,
                    home_team_id=1,
                    away_team_id=2,
                    kickoff_utc=datetime(2026, 8, 29, tzinfo=UTC),
                    status="SCHEDULED",
                ),
            ]
        )
        session.commit()
        metrics = TeamService(session).get_metrics(1, competition_season_id=1)
    engine.dispose()

    assert metrics.played == 4
    assert (metrics.won, metrics.drawn, metrics.lost) == (2, 1, 1)
    assert (metrics.goals_for, metrics.goals_against, metrics.goal_difference) == (6, 2, 4)
    assert metrics.points == 7
    assert metrics.form_last_5 == "WDLW"
    assert metrics.clean_sheets == 2
    assert metrics.failed_to_score == 1
    assert (metrics.home_played, metrics.home_points) == (2, 3)
    assert (metrics.away_played, metrics.away_points) == (2, 4)
    assert (metrics.home_won, metrics.home_drawn, metrics.home_lost) == (1, 0, 1)
    assert (metrics.away_won, metrics.away_drawn, metrics.away_lost) == (1, 1, 0)
    assert metrics.unbeaten_streak == 1
