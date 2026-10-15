"""Add player match-participation and positional statistics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007_player_match_statistics"
down_revision = "0006_team_coaches"
branch_labels = None
depends_on = None


def _add_missing(table: str, columns: tuple[sa.Column, ...]) -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing("players", (sa.Column("preferred_foot", sa.String(16), nullable=True),))
    _add_missing(
        "player_competition_season_statistics",
        (
            sa.Column("starts", sa.SmallInteger(), nullable=True),
            sa.Column("substitute_appearances", sa.SmallInteger(), nullable=True),
            sa.Column("shots_total", sa.SmallInteger(), nullable=True),
            sa.Column("shots_on_target", sa.SmallInteger(), nullable=True),
            sa.Column("goals_conceded", sa.SmallInteger(), nullable=True),
            sa.Column("clean_sheets", sa.SmallInteger(), nullable=True),
        ),
    )


def downgrade() -> None:
    op.drop_column("player_competition_season_statistics", "clean_sheets")
    op.drop_column("player_competition_season_statistics", "goals_conceded")
    op.drop_column("player_competition_season_statistics", "shots_on_target")
    op.drop_column("player_competition_season_statistics", "shots_total")
    op.drop_column("player_competition_season_statistics", "substitute_appearances")
    op.drop_column("player_competition_season_statistics", "starts")
    op.drop_column("players", "preferred_foot")
