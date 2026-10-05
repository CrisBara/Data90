import football_analytics.database.models  # noqa: F401
from football_analytics.database.base import Base


def test_initial_schema_registers_expected_tables() -> None:
    assert {
        "data_sources",
        "competitions",
        "seasons",
        "competition_seasons",
        "teams",
        "team_aliases",
        "source_matches",
        "matches",
        "match_team_statistics",
        "standings_snapshots",
        "standing_rows",
        "sync_runs",
        "data_quality_issues",
    }.issubset(Base.metadata.tables)


def test_unknown_match_statistics_are_nullable() -> None:
    table = Base.metadata.tables["match_team_statistics"]
    for name in ("possession", "shots_total", "corners", "passes_total"):
        assert table.c[name].nullable
