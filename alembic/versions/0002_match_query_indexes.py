"""Replace single-column match indexes with query-shaped composites."""

from alembic import op
from sqlalchemy import inspect

revision = "0002_match_query_indexes"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _index_names() -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes("matches")}


def upgrade() -> None:
    indexes = _index_names()
    for name in (
        "ix_matches_competition_season_id",
        "ix_matches_home_team_id",
        "ix_matches_away_team_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="matches")

    indexes = _index_names()
    if "ix_matches_competition_season_status" not in indexes:
        op.create_index(
            "ix_matches_competition_season_status",
            "matches",
            ["competition_season_id", "status"],
        )
    if "ix_matches_home_team_kickoff" not in indexes:
        op.create_index("ix_matches_home_team_kickoff", "matches", ["home_team_id", "kickoff_utc"])
    if "ix_matches_away_team_kickoff" not in indexes:
        op.create_index("ix_matches_away_team_kickoff", "matches", ["away_team_id", "kickoff_utc"])


def downgrade() -> None:
    indexes = _index_names()
    for name in (
        "ix_matches_away_team_kickoff",
        "ix_matches_home_team_kickoff",
        "ix_matches_competition_season_status",
    ):
        if name in indexes:
            op.drop_index(name, table_name="matches")

    indexes = _index_names()
    old_indexes = (
        ("ix_matches_away_team_id", "away_team_id"),
        ("ix_matches_home_team_id", "home_team_id"),
        ("ix_matches_competition_season_id", "competition_season_id"),
    )
    for name, column in old_indexes:
        if name not in indexes:
            op.create_index(name, "matches", [column])
            indexes.add(name)
