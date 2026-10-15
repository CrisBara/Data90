"""Competition scope helpers for event-aware daily synchronization."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from football_analytics.config.constants import COMPETITIONS
from football_analytics.database.models import Competition, CompetitionSeason, Match


def competitions_with_matches_on_date(
    session: Session,
    match_date: date,
    timezone_name: str,
) -> tuple[str, ...]:
    """Return known competition codes with a fixture on the user's local date."""
    zone = ZoneInfo(timezone_name)
    local_start = datetime.combine(match_date, time.min, tzinfo=zone)
    local_end = datetime.combine(match_date + timedelta(days=1), time.min, tzinfo=zone)
    start_utc = local_start.astimezone(UTC)
    end_utc = local_end.astimezone(UTC)
    rows = session.scalars(
        select(Competition.code)
        .join(CompetitionSeason, CompetitionSeason.competition_id == Competition.id)
        .join(Match, Match.competition_season_id == CompetitionSeason.id)
        .where(
            Competition.active.is_(True),
            Match.kickoff_utc >= start_utc,
            Match.kickoff_utc < end_utc,
        )
        .distinct()
    ).all()
    matches = set(rows)
    return tuple(definition.code for definition in COMPETITIONS if definition.code in matches)
