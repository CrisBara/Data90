"""Persist homepage featured-match priorities."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_featured_match_policy"
down_revision = "0009_promiedos_team_stats"
branch_labels = None
depends_on = None


POLICY = {
    "version": 1,
    "derby_bonus": 22,
    "derby_bonus_overrides": [
        {
            "competition_code": "PL",
            "teams": ["Brighton & Hove Albion FC", "Crystal Palace FC"],
            "bonus": 6,
        }
    ],
}


def upgrade() -> None:
    settings = sa.table(
        "app_settings",
        sa.column("key", sa.String(100)),
        sa.column("value", sa.JSON().with_variant(JSONB, "postgresql")),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    connection = op.get_bind()
    exists = connection.execute(
        sa.select(settings.c.key).where(settings.c.key == "featured_match_policy")
    ).first()
    if exists is None:
        connection.execute(
            sa.insert(settings).values(
                key="featured_match_policy",
                value=POLICY,
                updated_at=datetime.now(UTC),
            )
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM app_settings WHERE key = :key"),
        {"key": "featured_match_policy"},
    )
