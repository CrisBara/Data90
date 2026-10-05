from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from football_analytics.database.models import DataSource, SourceTeam, Team, TeamAlias
from football_analytics.domain.exceptions import EntityResolutionError
from football_analytics.sync.normalizer import normalize_name


@dataclass(frozen=True, slots=True)
class TeamResolution:
    team_id: int | None
    confidence: str
    ambiguous: bool = False


def resolve_team(session: Session, source_code: str, external_id: str, name: str) -> TeamResolution:
    """Resolve only exact source IDs or exact normalized names; never fuzzy-merge."""
    source_id = session.scalar(select(DataSource.id).where(DataSource.code == source_code))
    if source_id is None:
        raise EntityResolutionError(f"Fuente desconocida: {source_code}")

    source_team = session.scalar(
        select(SourceTeam).where(
            SourceTeam.source_id == source_id, SourceTeam.external_id == external_id
        )
    )
    if source_team:
        return TeamResolution(source_team.team_id, "source_id")

    normalized = normalize_name(name)
    alias_rows = session.scalars(
        select(TeamAlias).where(
            TeamAlias.normalized_alias == normalized,
            TeamAlias.source_id == source_id,
        )
    ).all()
    if not alias_rows:
        alias_rows = session.scalars(
            select(TeamAlias).where(
                TeamAlias.normalized_alias == normalized,
                TeamAlias.source_id.is_(None),
            )
        ).all()
    alias_ids = {row.team_id for row in alias_rows}
    if len(alias_ids) == 1:
        return TeamResolution(alias_ids.pop(), "alias")
    if len(alias_ids) > 1:
        return TeamResolution(None, "ambiguous", True)

    teams = session.scalars(select(Team).where(Team.canonical_name.is_not(None))).all()
    exact_ids = {team.id for team in teams if normalize_name(team.canonical_name) == normalized}
    if len(exact_ids) == 1:
        return TeamResolution(exact_ids.pop(), "canonical_name")
    if len(exact_ids) > 1:
        return TeamResolution(None, "ambiguous", True)
    return TeamResolution(None, "unresolved")
