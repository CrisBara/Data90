"""Store successful-tackle totals in player season statistics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_player_tackles_won"
down_revision = "0004_api_football_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("player_competition_season_statistics")
    }
    if "tackles_won" not in columns:
        op.add_column(
            "player_competition_season_statistics",
            sa.Column("tackles_won", sa.SmallInteger(), nullable=True),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("player_competition_season_statistics")
    }
    if "tackles_won" in columns:
        op.drop_column("player_competition_season_statistics", "tackles_won")
