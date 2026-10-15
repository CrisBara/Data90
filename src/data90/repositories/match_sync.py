from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    CompetitionSeason,
    DataQualityIssue,
    DataSource,
    Match,
    SourceMatch,
)
from data90.domain.dto import NormalizedMatch
from data90.domain.exceptions import EntityResolutionError
from data90.sync.validator import validate_match

MATCH_FIELDS = (
    "kickoff_utc",
    "status",
    "matchday",
    "round_label",
    "stage",
    "group_name",
    "leg",
    "venue_name",
    "home_score_ft",
    "away_score_ft",
    "home_score_ht",
    "away_score_ht",
    "home_score_et",
    "away_score_et",
    "home_score_pen",
    "away_score_pen",
)


@dataclass(slots=True)
class MatchUpsertResult:
    match: Match
    inserted: bool
    updated_fields: set[str] = field(default_factory=set)
    conflict_fields: set[str] = field(default_factory=set)


def _json_value(value: Any) -> dict[str, Any]:
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, datetime):
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    return {"value": value}


def _same_value(field_name: str, existing: Any, incoming: Any) -> bool:
    if (
        field_name == "kickoff_utc"
        and isinstance(existing, datetime)
        and isinstance(incoming, datetime)
    ):
        existing = (
            existing.replace(tzinfo=UTC) if existing.tzinfo is None else existing.astimezone(UTC)
        )
        incoming = (
            incoming.replace(tzinfo=UTC) if incoming.tzinfo is None else incoming.astimezone(UTC)
        )
    return existing == incoming


def _find_reconciliation_candidate(
    session: Session,
    dto: NormalizedMatch,
    competition_season_id: int,
    home_team_id: int,
    away_team_id: int,
) -> Match | None:
    candidates = session.scalars(
        select(Match).where(
            Match.competition_season_id == competition_season_id,
            Match.home_team_id == home_team_id,
            Match.away_team_id == away_team_id,
        )
    ).all()
    compatible = []
    for candidate in candidates:
        candidate_kickoff = candidate.kickoff_utc
        if candidate_kickoff.tzinfo is None:
            candidate_kickoff = candidate_kickoff.replace(tzinfo=dto.kickoff_utc.tzinfo)
        if dto.matchday is not None and candidate.matchday is not None:
            if dto.matchday != candidate.matchday:
                continue
        if dto.stage is not None and candidate.stage is not None and dto.stage != candidate.stage:
            continue
        if dto.group_name is not None and candidate.group_name is not None:
            if dto.group_name != candidate.group_name:
                continue
        if dto.matchday is None and dto.stage is None and dto.group_name is None:
            if abs(candidate_kickoff - dto.kickoff_utc) > timedelta(hours=36):
                continue
        compatible.append(candidate)
    return compatible[0] if len(compatible) == 1 else None


def _record_conflict(
    session: Session,
    match_id: int,
    field_name: str,
    primary_source_id: int,
    incoming_source_id: int,
    primary_value: Any,
    incoming_value: Any,
) -> None:
    value_a = _json_value(primary_value)
    value_b = _json_value(incoming_value)
    open_issues = session.scalars(
        select(DataQualityIssue).where(
            DataQualityIssue.entity_type == "match",
            DataQualityIssue.entity_id == match_id,
            DataQualityIssue.field_name == field_name,
            DataQualityIssue.source_a_id == primary_source_id,
            DataQualityIssue.source_b_id == incoming_source_id,
            DataQualityIssue.status == "OPEN",
        )
    ).all()
    if any(issue.value_a == value_a and issue.value_b == value_b for issue in open_issues):
        return
    session.add(
        DataQualityIssue(
            entity_type="match",
            entity_id=match_id,
            field_name=field_name,
            source_a_id=primary_source_id,
            source_b_id=incoming_source_id,
            value_a=value_a,
            value_b=value_b,
            severity="WARNING",
            status="OPEN",
        )
    )


def upsert_match(
    session: Session,
    dto: NormalizedMatch,
    *,
    competition_season_id: int,
    home_team_id: int,
    away_team_id: int,
) -> MatchUpsertResult:
    """Persist one normalized fixture idempotently while retaining source provenance."""
    validate_match(dto)
    source = session.scalar(select(DataSource).where(DataSource.code == dto.source_code))
    if source is None:
        raise EntityResolutionError(f"Fuente desconocida: {dto.source_code}")
    season = session.get(CompetitionSeason, competition_season_id)
    if season is None:
        raise EntityResolutionError(
            f"Temporada de competición inexistente: {competition_season_id}"
        )

    source_match = session.scalar(
        select(SourceMatch).where(
            SourceMatch.source_id == source.id,
            SourceMatch.external_id == dto.external_id,
        )
    )
    match = session.get(Match, source_match.match_id) if source_match else None
    if source_match and match is not None:
        same_identity = (
            match.competition_season_id == competition_season_id
            and match.home_team_id == home_team_id
            and match.away_team_id == away_team_id
        )
        if not same_identity:
            raise EntityResolutionError(
                "El mapping de partido existente apunta a otra competición o a otros equipos"
            )
    inserted = False
    if match is None:
        match = _find_reconciliation_candidate(
            session, dto, competition_season_id, home_team_id, away_team_id
        )
    if match is None:
        match = Match(
            competition_season_id=competition_season_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            kickoff_utc=dto.kickoff_utc,
            status=dto.status.value,
        )
        inserted = True
        session.add(match)
        session.flush()

    if source_match is None:
        source_match = SourceMatch(
            source_id=source.id,
            match_id=match.id,
            external_id=dto.external_id,
            source_updated_at=dto.source_updated_at,
        )
        session.add(source_match)
    elif dto.source_updated_at is not None:
        source_match.source_updated_at = dto.source_updated_at

    source_priorities = session.execute(
        select(DataSource.id, DataSource.priority)
        .join(SourceMatch, SourceMatch.source_id == DataSource.id)
        .where(SourceMatch.match_id == match.id)
    ).all()
    primary_source_id, primary_priority = min(source_priorities, key=lambda pair: pair.priority)
    existing_is_primary = source.id == primary_source_id
    result = MatchUpsertResult(match=match, inserted=inserted)

    incoming_values = {field_name: getattr(dto, field_name) for field_name in MATCH_FIELDS}
    incoming_values["status"] = dto.status.value
    for field_name, incoming_value in incoming_values.items():
        existing_value = getattr(match, field_name)
        if incoming_value is None:
            continue
        if _same_value(field_name, existing_value, incoming_value):
            continue
        if (
            existing_value is not None
            and not existing_is_primary
            and source.priority > primary_priority
        ):
            _record_conflict(
                session,
                match.id,
                field_name,
                primary_source_id,
                source.id,
                existing_value,
                incoming_value,
            )
            result.conflict_fields.add(field_name)
            continue
        setattr(match, field_name, incoming_value)
        result.updated_fields.add(field_name)

    if dto.source_updated_at is not None:
        match.source_updated_at = dto.source_updated_at
    return result
