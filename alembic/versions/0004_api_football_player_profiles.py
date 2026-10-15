"""Add player profiles and competition-season team memberships."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

import data90.database.models  # noqa: F401
from data90.database.base import Base

revision = "0004_api_football_profiles"
down_revision = "0003_players_and_scorers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("players")}
    additions = (
        ("nationality", sa.Column("nationality", sa.String(100), nullable=True)),
        ("birth_date", sa.Column("birth_date", sa.Date(), nullable=True)),
        ("birth_place", sa.Column("birth_place", sa.String(100), nullable=True)),
        ("height", sa.Column("height", sa.String(20), nullable=True)),
        ("weight", sa.Column("weight", sa.String(20), nullable=True)),
        ("position", sa.Column("position", sa.String(50), nullable=True)),
        ("photo_url", sa.Column("photo_url", sa.Text(), nullable=True)),
        (
            "profile_source_id",
            sa.Column("profile_source_id", sa.SmallInteger(), sa.ForeignKey("data_sources.id")),
        ),
        (
            "profile_updated_at",
            sa.Column("profile_updated_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("players", column)
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("player_team_seasons"):
        op.drop_table("player_team_seasons")
    columns = {column["name"] for column in inspect(bind).get_columns("players")}
    for name in (
        "profile_updated_at",
        "profile_source_id",
        "photo_url",
        "position",
        "weight",
        "height",
        "birth_place",
        "birth_date",
        "nationality",
    ):
        if name in columns:
            op.drop_column("players", name)
