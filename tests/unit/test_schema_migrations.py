import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from football_analytics.database.base import Base

MIGRATION_FILE = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0002_match_query_indexes.py"
)
PLAYER_MIGRATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0008_highlightly_player_match_stats.py"
)
_SPEC = importlib.util.spec_from_file_location("match_query_indexes", MIGRATION_FILE)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)
_PLAYER_SPEC = importlib.util.spec_from_file_location(
    "highlightly_player_match_stats", PLAYER_MIGRATION_FILE
)
assert _PLAYER_SPEC is not None and _PLAYER_SPEC.loader is not None
_PLAYER_MIGRATION = importlib.util.module_from_spec(_PLAYER_SPEC)
_PLAYER_SPEC.loader.exec_module(_PLAYER_MIGRATION)
EXPECTED_INDEXES = {
    "ix_matches_competition_season_status",
    "ix_matches_home_team_kickoff",
    "ix_matches_away_team_kickoff",
}
INSERT_CONTROL_ROW = text(
    "INSERT INTO competitions "
    "(id, code, name, created_at, updated_at) "
    "VALUES (1, 'PL', 'League', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)
OLD_INDEXES = {
    "ix_matches_competition_season_id",
    "ix_matches_home_team_id",
    "ix_matches_away_team_id",
}


def _upgrade(connection) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        _MIGRATION.upgrade()


def _index_names(connection) -> set[str]:
    return {index["name"] for index in inspect(connection).get_indexes("matches")}


def test_match_index_migration_accepts_fresh_schema_and_preserves_rows() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _upgrade(connection)
        assert EXPECTED_INDEXES <= _index_names(connection)
        connection.execute(INSERT_CONTROL_ROW)
        assert connection.execute(text("SELECT count(*) FROM competitions")).scalar_one() == 1
    engine.dispose()


def test_match_index_migration_replaces_legacy_indexes_and_preserves_rows() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for index_name in EXPECTED_INDEXES:
            connection.execute(text(f'DROP INDEX "{index_name}"'))
        for index_name, column in (
            ("ix_matches_competition_season_id", "competition_season_id"),
            ("ix_matches_home_team_id", "home_team_id"),
            ("ix_matches_away_team_id", "away_team_id"),
        ):
            connection.execute(text(f'CREATE INDEX "{index_name}" ON matches ("{column}")'))
        _upgrade(connection)
        names = _index_names(connection)
        assert EXPECTED_INDEXES <= names
        assert OLD_INDEXES.isdisjoint(names)
        connection.execute(INSERT_CONTROL_ROW)
        assert connection.execute(text("SELECT count(*) FROM competitions")).scalar_one() == 1
    engine.dispose()


def test_highlightly_player_migration_accepts_model_created_table() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            _PLAYER_MIGRATION.upgrade()
        assert "player_match_statistics" in inspect(connection).get_table_names()
        names = {
            index["name"] for index in inspect(connection).get_indexes("player_match_statistics")
        }
        assert "ix_player_match_statistics_player_match" in names
    engine.dispose()


def test_highlightly_player_migration_creates_table_on_legacy_schema() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE player_match_statistics")
        with Operations.context(MigrationContext.configure(connection)):
            _PLAYER_MIGRATION.upgrade()
        assert "player_match_statistics" in inspect(connection).get_table_names()
    engine.dispose()
