from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from data90.database.base import Base
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchEvent,
    Player,
    PlayerCompetitionSeasonStatistic,
    PlayerTeamSeason,
    Season,
    SourcePlayer,
    Team,
)
from data90.domain.dto import NormalizedPlayerSeasonStatistic
from data90.repositories.players import (
    _same_verified_player_name,
    goal_events_for_competition_season,
    player_profile_detail,
    player_statistics_for_competition_season,
    team_squad_for_team,
    upsert_player_statistic,
)


def test_player_statistics_upsert_is_idempotent_and_preserves_unknown_values() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        source = DataSource(
            id=1, code="openligadb", name="OpenLigaDB", priority=2, requires_api_key=False
        )
        competition = Competition(code="UEL", name="Europa League")
        season = Season(label="2026/27", start_year=2026, end_year=2027, active=True)
        session.add_all((source, competition, season))
        session.flush()
        competition_season = CompetitionSeason(
            competition_id=competition.id, season_id=season.id, status="ACTIVE"
        )
        session.add(competition_season)
        session.flush()
        dto = NormalizedPlayerSeasonStatistic(
            source_code="openligadb",
            external_id="42",
            display_name="Forward Example",
            competition_code="UEL",
            season_start_year=2026,
            goals=7,
            appearances=10,
            starts=8,
            substitute_appearances=2,
            minutes_played=765,
            shots_total=21,
            shots_on_target=9,
        )

        player, inserted, updated = upsert_player_statistic(
            session, dto, competition_season_id=competition_season.id
        )
        assert inserted and not updated
        same_player, inserted_again, updated_again = upsert_player_statistic(
            session, dto, competition_season_id=competition_season.id
        )

        assert same_player.id == player.id
        assert not inserted_again and not updated_again
        row = session.scalar(select(PlayerCompetitionSeasonStatistic))
        assert row is not None
        assert row.goals == 7
        assert (row.starts, row.substitute_appearances) == (8, 2)
        assert row.assists is None
        assert session.scalar(select(func.count()).select_from(SourcePlayer)) == 1
        items = player_statistics_for_competition_season(session, competition_season.id)
        assert len(items) == 1
        assert (items[0].display_name, items[0].goals) == ("Forward Example", 7)
        player = session.scalar(select(Player))
        assert player is not None
        player.preferred_foot = "RIGHT"
        player.nationality = "Argentina"
        player.position = "Attacker"
        player.birth_date = datetime(2000, 1, 1, tzinfo=UTC).date()
        player.profile_source_id = source.id
        player.profile_updated_at = datetime.now(UTC)

        home = Team(canonical_name="Arsenal")
        away = Team(canonical_name="Chelsea")
        session.add_all((home, away))
        session.flush()
        session.add(
            PlayerTeamSeason(
                player_id=player.id,
                team_id=home.id,
                competition_season_id=competition_season.id,
                source_id=source.id,
                shirt_number=10,
                position="Attacker",
            )
        )
        profile = player_profile_detail(session, player.id)
        assert profile is not None
        assert (profile.nationality, profile.preferred_foot) == ("Argentina", "RIGHT")
        assert profile.profile_source_code == "openligadb"
        assert profile.profile_updated_at is not None
        assert profile.team_competitions[0].shirt_number == 10
        season_stats = profile.statistics[0]
        assert (season_stats.starts, season_stats.substitute_appearances) == (8, 2)
        assert (season_stats.shots_total, season_stats.shots_on_target) == (21, 9)
        assert season_stats.sources_by_field["goals"] == "openligadb"
        assert season_stats.updated_by_field["goals"] is not None
        match = Match(
            competition_season_id=competition_season.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_utc=datetime(2026, 8, 1, 16, 0, tzinfo=UTC),
            status="FINISHED",
        )
        session.add(match)
        session.flush()
        session.add(
            MatchEvent(
                match_id=match.id,
                team_id=home.id,
                event_type="GOAL",
                minute=24,
                player_name=player.display_name,
                player_id=player.id,
                source_id=source.id,
                external_id="9001:1",
            )
        )
        events = goal_events_for_competition_season(session, competition_season.id)
        assert len(events) == 1
        assert (events[0].home_team, events[0].away_team) == ("Arsenal", "Chelsea")
        assert (events[0].minute, events[0].player_name, events[0].team_name) == (
            24,
            "Forward Example",
            "Arsenal",
        )
    engine.dispose()


def test_squad_prefers_verified_identity_when_exact_name_has_conflicting_birthdays() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        roster_source = DataSource(
            id=1,
            code="football-data",
            name="Football-data.org",
            priority=1,
            requires_api_key=True,
        )
        pitchapi = DataSource(
            id=2,
            code="pitchapi",
            name="PitchAPI",
            priority=8,
            requires_api_key=True,
        )
        competition = Competition(code="PL", name="Premier League")
        season = Season(label="2026/27", start_year=2026, end_year=2027, active=True)
        team = Team(canonical_name="AFC Bournemouth")
        session.add_all((roster_source, pitchapi, competition, season, team))
        session.flush()
        competition_season = CompetitionSeason(
            competition_id=competition.id, season_id=season.id, status="ACTIVE"
        )
        session.add(competition_season)
        session.flush()
        duplicate = Player(display_name="António Silva", birth_date=datetime(2003, 10, 30).date())
        verified = Player(display_name="Antonio Silva", birth_date=datetime(2003, 10, 31).date())
        session.add_all((duplicate, verified))
        session.flush()
        session.add_all(
            (
                PlayerTeamSeason(
                    player_id=duplicate.id,
                    team_id=team.id,
                    competition_season_id=competition_season.id,
                    source_id=roster_source.id,
                ),
                PlayerTeamSeason(
                    player_id=verified.id,
                    team_id=team.id,
                    competition_season_id=competition_season.id,
                    source_id=roster_source.id,
                ),
                SourcePlayer(
                    player_id=verified.id,
                    source_id=pitchapi.id,
                    external_id="pitch-player-1",
                ),
            )
        )

        squad = team_squad_for_team(session, team.id)

        assert len(squad) == 1
        assert squad[0].player_id == verified.id
        assert squad[0].birth_date == verified.birth_date
    engine.dispose()


def test_verified_player_alias_accepts_longer_source_name() -> None:
    assert _same_verified_player_name("Mohamed Bachir Belloumi", "Mohamed Belloumi")
    assert _same_verified_player_name("Micky van de Ven", "Mickey van de Ven")
    assert not _same_verified_player_name("Joe Willock", "Joelinton")
