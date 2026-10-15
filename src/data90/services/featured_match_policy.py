"""Load the persistent homepage featured-match policy."""

from __future__ import annotations

from data90.config.featured_match_priority import (
    DEFAULT_FEATURED_MATCH_POLICY,
    FEATURED_MATCH_POLICY_SETTING,
)
from data90.database.models import AppSetting


def featured_match_policy(session) -> dict:
    """Return the database policy, with code defaults for older databases."""
    setting = session.get(AppSetting, FEATURED_MATCH_POLICY_SETTING)
    policy = dict(DEFAULT_FEATURED_MATCH_POLICY)
    policy["derby_bonus_overrides"] = list(
        DEFAULT_FEATURED_MATCH_POLICY["derby_bonus_overrides"]
    )
    if setting is not None and isinstance(setting.value, dict):
        policy.update(setting.value)
    return policy
