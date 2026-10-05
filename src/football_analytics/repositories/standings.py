from collections.abc import Iterable

from sqlalchemy.orm import Session

from football_analytics.database.models import StandingRow, StandingsSnapshot
from football_analytics.domain.dto import NormalizedStanding


def add_standings_snapshot(
    session: Session,
    *,
    competition_season_id: int,
    source_id: int,
    team_ids_by_external_id: dict[str, int],
    rows: Iterable[NormalizedStanding],
) -> int:
    """Persist an immutable snapshot for a provider's current table."""
    standings = list(rows)
    if not standings:
        return 0
    first = standings[0]
    snapshot = StandingsSnapshot(
        competition_season_id=competition_season_id,
        source_id=source_id,
        scope=first.scope,
        group_name=first.group_name,
        matchday=first.matchday,
    )
    session.add(snapshot)
    session.flush()
    inserted = 0
    for standing in standings:
        team_id = team_ids_by_external_id.get(standing.team_external_id)
        if team_id is None:
            continue
        session.add(
            StandingRow(
                snapshot_id=snapshot.id,
                team_id=team_id,
                position=standing.position,
                played=standing.played,
                won=standing.won,
                drawn=standing.drawn,
                lost=standing.lost,
                goals_for=standing.goals_for,
                goals_against=standing.goals_against,
                goal_difference=standing.goal_difference,
                points=standing.points,
                form=standing.form,
            )
        )
        inserted += 1
    if inserted == 0:
        session.delete(snapshot)
    return inserted
