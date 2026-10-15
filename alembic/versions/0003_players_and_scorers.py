"""Add provider-backed player identities and seasonal statistics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

import football_analytics.database.models  # noqa: F401
from football_analytics.database.base import Base

revision = "0003_players_and_scorers"
down_revision = "0002_match_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)
    columns = {column["name"] for column in inspect(bind).get_columns("match_events")}
    if "player_id" not in columns:
        op.add_column(
            "match_events",
            sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("players.id"), nullable=True),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("match_events")}
    if "ix_match_events_player_id" not in indexes:
        op.create_index("ix_match_events_player_id", "match_events", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_match_events_player_id", table_name="match_events")
    op.drop_column("match_events", "player_id")
    bind = op.get_bind()
    for table in (
        "player_competition_season_statistics",
        "source_players",
        "players",
    ):
        if inspect(bind).has_table(table):
            op.drop_table(table)
