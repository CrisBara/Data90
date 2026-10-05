from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from football_analytics.database.base import Base
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataQualityIssue,
    DataSource,
    Match,
    Season,
    SourceMatch,
    Team,
)
from football_analytics.domain.dto import NormalizedMatch
from football_analytics.domain.enums import MatchStatus
from football_analytics.repositories.match_sync import upsert_match


def _dto(**changes: object) -> NormalizedMatch:
    values: dict[str, object] = {
        "source_code": "football-data",
        "external_id": "match-1",
        "competition_code": "PL",
        "season_start_year": 2026,
        "kickoff_utc": datetime(2026, 8, 1, 16, tzinfo=UTC),
        "home_external_id": "home",
        "away_external_id": "away",
        "home_name": "Home FC",
        "away_name": "Away FC",
        "status": MatchStatus.SCHEDULED,
        "matchday": 1,
    }
    values.update(changes)
    return NormalizedMatch(**values)  # type: ignore[arg-type]


def _database() -> tuple[object, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            DataSource(
                id=1, code="football-data", name="Football Data", priority=1, requires_api_key=True
            ),
            DataSource(
                id=2, code="openligadb", name="OpenLigaDB", priority=2, requires_api_key=False
            ),
            Competition(id=1, code="PL", name="Premier League"),
            Season(id=1, label="2026/27", start_year=2026, end_year=2027),
            CompetitionSeason(id=1, competition_id=1, season_id=1, status="ACTIVE"),
            Team(id=1, canonical_name="Home FC"),
            Team(id=2, canonical_name="Away FC"),
        ]
    )
    session.commit()
    return engine, session


def test_duplicate_sync_is_idempotent_and_reschedule_keeps_match_id() -> None:
    _, session = _database()
    first = upsert_match(session, _dto(), competition_season_id=1, home_team_id=1, away_team_id=2)
    session.commit()
    match_id = first.match.id
    second = upsert_match(session, _dto(), competition_season_id=1, home_team_id=1, away_team_id=2)
    session.commit()
    rescheduled = upsert_match(
        session,
        _dto(kickoff_utc=datetime(2026, 8, 1, 18, tzinfo=UTC)),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()

    assert first.inserted
    assert not second.inserted
    assert match_id == second.match.id == rescheduled.match.id
    assert rescheduled.match.kickoff_utc.replace(tzinfo=UTC) == datetime(2026, 8, 1, 18, tzinfo=UTC)
    assert session.scalar(select(Match.id).where(Match.id == match_id)) == match_id
    assert len(session.scalars(select(SourceMatch)).all()) == 1
    session.close()


def test_null_does_not_overwrite_known_score_and_same_source_can_correct_score() -> None:
    _, session = _database()
    original = upsert_match(
        session,
        _dto(status=MatchStatus.FINISHED, home_score_ft=2, away_score_ft=1),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()
    null_update = upsert_match(
        session,
        _dto(status=MatchStatus.FINISHED, home_score_ft=None, away_score_ft=None),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    assert null_update.match.home_score_ft == 2
    correction = upsert_match(
        session,
        _dto(status=MatchStatus.FINISHED, home_score_ft=3, away_score_ft=1),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()

    assert correction.match.id == original.match.id
    assert correction.match.home_score_ft == 3
    session.close()


def test_known_result_replaces_null_and_zero_remains_a_known_score() -> None:
    _, session = _database()
    fixture = upsert_match(session, _dto(), competition_season_id=1, home_team_id=1, away_team_id=2)
    session.commit()

    result = upsert_match(
        session,
        _dto(status=MatchStatus.FINISHED, home_score_ft=0, away_score_ft=0),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()

    assert result.match.id == fixture.match.id
    assert result.match.home_score_ft == 0
    assert result.match.away_score_ft == 0
    session.close()


def test_postponed_and_cancelled_statuses_keep_the_match_record() -> None:
    _, session = _database()
    fixture = upsert_match(session, _dto(), competition_season_id=1, home_team_id=1, away_team_id=2)
    session.commit()
    match_id = fixture.match.id

    postponed = upsert_match(
        session,
        _dto(status=MatchStatus.POSTPONED),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()
    cancelled = upsert_match(
        session,
        _dto(status=MatchStatus.CANCELLED),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()

    assert postponed.match.id == cancelled.match.id == match_id
    assert cancelled.match.status == MatchStatus.CANCELLED.value
    assert session.scalar(select(Match.id).where(Match.id == match_id)) == match_id
    assert len(session.scalars(select(Match)).all()) == 1
    session.close()


def test_lower_priority_conflict_is_recorded_without_overwrite() -> None:
    _, session = _database()
    original = upsert_match(
        session,
        _dto(status=MatchStatus.FINISHED, home_score_ft=2, away_score_ft=1),
        competition_season_id=1,
        home_team_id=1,
        away_team_id=2,
    )
    session.commit()
    secondary = replace(
        _dto(status=MatchStatus.FINISHED, home_score_ft=4, away_score_ft=1),
        source_code="openligadb",
        external_id="ol-match-1",
        kickoff_utc=original.match.kickoff_utc.replace(tzinfo=UTC),
    )
    result = upsert_match(
        session, secondary, competition_season_id=1, home_team_id=1, away_team_id=2
    )
    session.commit()

    issues = session.scalars(select(DataQualityIssue)).all()
    assert result.match.id == original.match.id
    assert result.match.home_score_ft == 2
    assert result.conflict_fields == {"home_score_ft"}
    assert len(issues) == 1
    assert issues[0].field_name == "home_score_ft"
    session.close()
