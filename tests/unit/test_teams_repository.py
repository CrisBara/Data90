from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from football_analytics.database.base import Base
from football_analytics.database.models import Competition, CompetitionSeason, Match, Season, Team
from football_analytics.repositories.teams import search_teams, team_match_history


def test_team_search_and_history_use_local_competition_matches() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        competition = Competition(code="PL", name="Premier League", display_order=1)
        season = Season(label="2026/27", start_year=2026, end_year=2027)
        home = Team(canonical_name="Town FC", country_name="England")
        away = Team(canonical_name="City FC", country_name="England")
        session.add_all([competition, season, home, away])
        session.flush()
        competition_season = CompetitionSeason(
            competition_id=competition.id,
            season_id=season.id,
            status="ACTIVE",
        )
        session.add(competition_season)
        session.flush()
        session.add(
            Match(
                competition_season_id=competition_season.id,
                home_team_id=home.id,
                away_team_id=away.id,
                kickoff_utc=datetime(2026, 10, 2, 16, tzinfo=UTC),
                status="FINISHED",
                home_score_ft=2,
                away_score_ft=1,
            )
        )

    with factory() as session:
        teams = search_teams(session, "Town")
        history = team_match_history(session, teams[0].team_id)

    assert len(teams) == 1
    assert teams[0].name == "Town FC"
    assert teams[0].match_count == 1
    assert teams[0].competition_count == 1
    assert history[0].opponent == "City FC"
    assert history[0].venue == "Local"
    assert (history[0].goals_for, history[0].goals_against) == (2, 1)
    engine.dispose()
