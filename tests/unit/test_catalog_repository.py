from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from data90.database.base import Base
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Season,
    SourceCompetitionSeason,
    SourceTeam,
    Team,
    TeamAlias,
)
from data90.domain.dto import NormalizedSeason, NormalizedTeam
from data90.repositories.catalog import upsert_competition_season, upsert_team


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            DataSource(id=1, code="fixture", name="Fixture", priority=1, requires_api_key=False),
            Competition(id=1, code="PL", name="Premier League"),
        ]
    )
    session.commit()
    return session


def test_new_season_is_independent_and_source_mapping_is_idempotent() -> None:
    session = _session()
    dto = NormalizedSeason(
        source_code="fixture",
        competition_code="PL",
        external_id="season-2026",
        external_code="PL",
        external_season="2026",
        start_year=2026,
        end_year=2027,
        start_date=date(2026, 8, 1),
        end_date=date(2027, 5, 31),
    )
    first = upsert_competition_season(session, dto)
    session.commit()
    second = upsert_competition_season(session, dto)
    session.commit()

    assert first.id == second.id
    assert session.scalar(select(Season.label)) == "2026/27"
    assert len(session.scalars(select(CompetitionSeason)).all()) == 1
    assert len(session.scalars(select(SourceCompetitionSeason)).all()) == 1
    session.close()
    session.get_bind().dispose()


def test_team_import_creates_mapping_and_normalized_aliases() -> None:
    session = _session()
    dto = NormalizedTeam(
        source_code="fixture",
        external_id="team-10",
        canonical_name="Atlético de Madrid",
        short_name="Atleti",
        aliases=("Atletico Madrid",),
    )
    team = upsert_team(session, dto)
    session.commit()
    same_team = upsert_team(session, dto)
    session.commit()

    assert team.id == same_team.id
    assert len(session.scalars(select(Team)).all()) == 1
    assert len(session.scalars(select(SourceTeam)).all()) == 1
    aliases = {item.normalized_alias for item in session.scalars(select(TeamAlias)).all()}
    assert "atletico de madrid" in aliases
    assert "atleti" in aliases
    session.close()
    session.get_bind().dispose()
