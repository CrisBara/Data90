from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from football_analytics.database.base import Base
from football_analytics.database.models import DataSource, SourceTeam, Team, TeamAlias
from football_analytics.sync.resolver import resolve_team


def test_source_team_mapping_has_highest_priority() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                DataSource(
                    id=1, code="fixture", name="Fixture", priority=1, requires_api_key=False
                ),
                Team(id=1, canonical_name="Manchester City"),
                Team(id=2, canonical_name="Manchester City Women"),
                SourceTeam(id=1, source_id=1, team_id=1, external_id="team-1"),
                TeamAlias(
                    id=1, team_id=2, source_id=1, alias="Man City", normalized_alias="man city"
                ),
            ]
        )
        session.commit()
        result = resolve_team(session, "fixture", "team-1", "Man City")
    assert result.team_id == 1
    assert result.confidence == "source_id"


def test_ambiguous_exact_alias_is_not_merged() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                DataSource(
                    id=1, code="fixture", name="Fixture", priority=1, requires_api_key=False
                ),
                Team(id=1, canonical_name="Alpha FC"),
                Team(id=2, canonical_name="Beta FC"),
                TeamAlias(
                    id=1, team_id=1, source_id=None, alias="United", normalized_alias="united"
                ),
                TeamAlias(
                    id=2, team_id=2, source_id=None, alias="United", normalized_alias="united"
                ),
            ]
        )
        session.commit()
        result = resolve_team(session, "fixture", "unknown", "United")
    assert result.team_id is None
    assert result.ambiguous
