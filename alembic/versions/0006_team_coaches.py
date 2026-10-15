"""Persist current team coaches with source attribution."""

from alembic import op
from sqlalchemy import inspect

import football_analytics.database.models  # noqa: F401
from football_analytics.database.base import Base

revision = "0006_team_coaches"
down_revision = "0005_player_tackles_won"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("team_coaches"):
        op.drop_table("team_coaches")
