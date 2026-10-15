from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data90.database.base import Base
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Season,
    StandingRow,
    StandingsSnapshot,
    Team,
)
from data90.repositories.competitions import (
    competition_list,
    competition_seasons,
    matches_for_competition_season,
    standings_for_competition_season,
)


def test_competition_queries_return_local_fixtures_and_standings() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        source = DataSource(
            id=1,
            code="fixture",
            name="Fixture",
            priority=1,
            requires_api_key=False,
        )
        competition = Competition(code="PL", name="Premier League", display_order=1)
        season = Season(label="2026/27", start_year=2026, end_year=2027, active=True)
        home = Team(canonical_name="Home FC")
        away = Team(canonical_name="Away FC")
        session.add_all([source, competition, season, home, away])
        session.flush()
        competition_season = CompetitionSeason(
            competition_id=competition.id,
            season_id=season.id,
            status="ACTIVE",
            last_synced_at=datetime(2026, 10, 1, tzinfo=UTC),
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
                matchday=1,
            )
        )
        session.flush()
        snapshot = StandingsSnapshot(
            competition_season_id=competition_season.id,
            source_id=source.id,
            scope="TOTAL",
            snapshot_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add_all(
            [
                StandingRow(
                    snapshot_id=snapshot.id,
                    team_id=home.id,
                    position=1,
                    played=1,
                    won=1,
                    drawn=0,
                    lost=0,
                    goals_for=2,
                    goals_against=1,
                    goal_difference=1,
                    points=3,
                ),
                StandingRow(
                    snapshot_id=snapshot.id,
                    team_id=away.id,
                    position=2,
                    played=1,
                    won=0,
                    drawn=0,
                    lost=1,
                    goals_for=1,
                    goals_against=2,
                    goal_difference=-1,
                    points=0,
                ),
            ]
        )
        newer_home_snapshot = StandingsSnapshot(
            competition_season_id=competition_season.id,
            source_id=source.id,
            scope="HOME",
            snapshot_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
        session.add(newer_home_snapshot)
        session.flush()
        session.add(
            StandingRow(
                snapshot_id=newer_home_snapshot.id,
                team_id=away.id,
                position=1,
                played=1,
                won=1,
                drawn=0,
                lost=0,
                goals_for=5,
                goals_against=0,
                goal_difference=5,
                points=3,
            )
        )

    with factory() as session:
        competitions = competition_list(session)
        seasons = competition_seasons(session, competitions[0].competition_id)
        matches = matches_for_competition_season(session, seasons[0].competition_season_id)
        standings = standings_for_competition_season(session, seasons[0].competition_season_id)

    assert competitions[0].name == "Premier League"
    assert competitions[0].team_count == 2
    assert competitions[0].match_count == 1
    assert matches[0].home_team == "Home FC"
    assert matches[0].home_score_ft == 2
    assert standings[0].team_name == "Home FC"
    assert standings[0].points == 3
    engine.dispose()
