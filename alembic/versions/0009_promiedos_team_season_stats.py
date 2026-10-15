"""Store Promiedos team season aggregates separately from match statistics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0009_promiedos_team_stats"
down_revision = "0008_highlightly_match_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "team_competition_season_statistics" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "team_competition_season_statistics",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
            sa.Column(
                "team_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
            ),
            sa.Column(
                "competition_season_id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.Column("source_id", sa.SmallInteger(), nullable=False),
            sa.Column("goals_average", sa.Numeric(6, 2)),
            sa.Column("goals_conceded_average", sa.Numeric(6, 2)),
            sa.Column("clean_sheets", sa.SmallInteger()),
            sa.Column("possession_percentage", sa.Numeric(5, 2)),
            sa.Column("corners_average", sa.Numeric(6, 2)),
            sa.Column("penalties_scored", sa.SmallInteger()),
            sa.Column("penalties_attempted", sa.SmallInteger()),
            sa.Column("penalties_conceded", sa.SmallInteger()),
            sa.Column("penalties_conceded_attempted", sa.SmallInteger()),
            sa.Column("yellow_cards", sa.SmallInteger()),
            sa.Column("red_cards", sa.SmallInteger()),
            sa.Column("source_updated_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
            sa.ForeignKeyConstraint(["competition_season_id"], ["competition_seasons.id"]),
            sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"]),
            sa.UniqueConstraint("team_id", "competition_season_id", "source_id"),
        )
    indexes = {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes("team_competition_season_statistics")
    }
    if "ix_team_competition_season_stats_season" not in indexes:
        op.create_index(
            "ix_team_competition_season_stats_season",
            "team_competition_season_statistics",
            ["competition_season_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_team_competition_season_stats_season",
        table_name="team_competition_season_statistics",
    )
    op.drop_table("team_competition_season_statistics")
