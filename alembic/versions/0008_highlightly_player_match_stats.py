"""Store provider player box scores by match."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_highlightly_match_stats"
down_revision = "0007_player_match_statistics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "player_match_statistics" not in inspect(bind).get_table_names():
        op.create_table(
            "player_match_statistics",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
            sa.Column(
                "match_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
            ),
            sa.Column(
                "player_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
            ),
            sa.Column(
                "team_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
            ),
            sa.Column("source_id", sa.SmallInteger(), nullable=False),
            sa.Column("minutes_played", sa.SmallInteger()),
            sa.Column("is_substitute", sa.Boolean()),
            sa.Column("shirt_number", sa.SmallInteger()),
            sa.Column("position", sa.String(50)),
            sa.Column("goals", sa.SmallInteger()),
            sa.Column("assists", sa.SmallInteger()),
            sa.Column("yellow_cards", sa.SmallInteger()),
            sa.Column("red_cards", sa.SmallInteger()),
            sa.Column("shots_total", sa.SmallInteger()),
            sa.Column("shots_on_target", sa.SmallInteger()),
            sa.Column("goals_conceded", sa.SmallInteger()),
            sa.Column("clean_sheet", sa.Boolean()),
            sa.Column("goalkeeper_saves", sa.SmallInteger()),
            sa.Column("source_updated_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
            sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"]),
            sa.UniqueConstraint("match_id", "player_id", "source_id"),
        )
    indexes = {index["name"] for index in inspect(bind).get_indexes("player_match_statistics")}
    if "ix_player_match_statistics_player_match" not in indexes:
        op.create_index(
            "ix_player_match_statistics_player_match",
            "player_match_statistics",
            ["player_id", "match_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_player_match_statistics_player_match", table_name="player_match_statistics")
    op.drop_table("player_match_statistics")
