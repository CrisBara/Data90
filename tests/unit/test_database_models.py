import data90.database.models  # noqa: F401
from data90.database.base import Base


def test_initial_schema_registers_expected_tables() -> None:
    assert {
        "data_sources",
        "competitions",
        "seasons",
        "competition_seasons",
        "teams",
        "team_aliases",
        "players",
        "source_players",
        "player_competition_season_statistics",
        "player_team_seasons",
        "source_matches",
        "matches",
        "match_team_statistics",
        "player_match_statistics",
        "standings_snapshots",
        "standing_rows",
        "sync_runs",
        "data_quality_issues",
    }.issubset(Base.metadata.tables)


def test_unknown_match_statistics_are_nullable() -> None:
    table = Base.metadata.tables["match_team_statistics"]
    for name in ("possession", "shots_total", "corners", "passes_total"):
        assert table.c[name].nullable


def test_player_profile_fields_are_nullable_for_partial_provider_coverage() -> None:
    table = Base.metadata.tables["players"]
    for name in ("nationality", "birth_date", "height", "weight", "position", "photo_url"):
        assert table.c[name].nullable
