from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from football_analytics.database.base import Base
from football_analytics.database.models import (
    AppSetting,
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Player,
    PlayerCompetitionSeasonStatistic,
    PlayerMatchStatistic,
    Season,
    SourceMatch,
    Team,
    TeamAlias,
)
from football_analytics.services.highlightly_match_sync import (
    DAILY_REQUEST_BUDGET,
    DailyQuotaReached,
    _reserve_daily_request,
    sync_highlightly_match_statistics,
)


class FakeHighlightlyProvider:
    request_count = 0

    def get_matches(self, **_kwargs):
        self.request_count += 1
        return {
            "data": [
                {
                    "id": 1001,
                    "date": "2026-09-19T18:00:00.000Z",
                    "homeTeam": {"id": 11, "name": "Inter"},
                    "awayTeam": {"id": 22, "name": "Roma"},
                    "state": {
                        "description": "Finished",
                        "score": {"current": "2 - 1"},
                    },
                }
            ],
            "pagination": {"totalCount": 1},
        }

    def get_box_score(self, _match_id):
        self.request_count += 1
        return [
            {
                "team": {"id": 11, "name": "Inter"},
                "players": [
                    {
                        "id": 77,
                        "fullName": "Test Goalkeeper",
                        "position": "Goalkeeper",
                        "shirtNumber": 1,
                        "minutesPlayed": 90,
                        "isSubstitute": False,
                        "statistics": {
                            "goalsScored": 0,
                            "assists": 0,
                            "cardsYellow": 0,
                            "cardsRed": 0,
                            "shotsTotal": 0,
                            "shotsOnTarget": 0,
                            "saves": 3,
                        },
                    }
                ],
            }
        ]

    def get_player_profile(self, _player_id):
        self.request_count += 1
        return {
            "fullName": "Test Goalkeeper",
            "profile": {
                "birthDate": "Feb 2, 2000",
                "citizenship": "Italy",
                "foot": "right",
                "position": {"main": "Goalkeeper"},
            },
        }


def test_highlightly_box_score_is_persisted_and_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        source = DataSource(
            id=1,
            code="highlightly",
            name="Highlightly",
            priority=5,
            requires_api_key=True,
        )
        competition = Competition(code="SERIE_A", name="Serie A")
        season = Season(label="2026/27", start_year=2026, end_year=2027, active=True)
        home = Team(canonical_name="Inter")
        away = Team(canonical_name="Roma")
        session.add_all([source, competition, season, home, away])
        session.flush()
        competition_season = CompetitionSeason(
            competition_id=competition.id,
            season_id=season.id,
            status="IN_PROGRESS",
        )
        session.add(competition_season)
        session.flush()
        session.add_all(
            [
                TeamAlias(team_id=home.id, alias="Inter", normalized_alias="inter"),
                TeamAlias(team_id=away.id, alias="Roma", normalized_alias="roma"),
                Match(
                    competition_season_id=competition_season.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff_utc=datetime(2026, 9, 19, 18, tzinfo=UTC),
                    status="FINISHED",
                    home_score_ft=2,
                    away_score_ft=1,
                ),
            ]
        )

    provider = FakeHighlightlyProvider()
    first = sync_highlightly_match_statistics(factory, provider=provider)
    second = sync_highlightly_match_statistics(factory, provider=provider)

    assert first.inserted_count == 1
    assert first.message.find("1 perfiles enriquecidos") >= 0
    assert second.inserted_count == 0
    assert second.requests_count == 4
    with factory() as session:
        stat = session.scalar(select(PlayerMatchStatistic))
        season_stat = session.scalar(select(PlayerCompetitionSeasonStatistic))
        source_match = session.scalar(select(SourceMatch))
        player = session.scalar(select(PlayerMatchStatistic))
        profile = session.get(Player, player.player_id) if player else None
        assert stat is not None and stat.minutes_played == 90
        assert stat.goals_conceded == 1 and stat.clean_sheet is False
        assert season_stat is not None
        assert season_stat.appearances == 1 and season_stat.starts == 1
        assert season_stat.goals == 0 and season_stat.clean_sheets == 0
        assert profile is not None
        assert profile.nationality == "Italy" and profile.preferred_foot == "right"
        assert source_match is not None and source_match.raw_hash
    engine.dispose()


def test_highlightly_daily_quota_blocks_requests_at_the_reserved_limit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            AppSetting(
                key="highlightly_daily_request_usage",
                value={
                    "date": datetime.now(UTC).date().isoformat(),
                    "used": DAILY_REQUEST_BUDGET,
                },
            )
        )

    with pytest.raises(DailyQuotaReached):
        _reserve_daily_request(factory)
    engine.dispose()
