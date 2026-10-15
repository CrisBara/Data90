"""Merge verified duplicate club entities that duplicate squad appearances.

Dry-run by default. ``--apply`` writes a compressed backup and performs one
transaction. The static map only contains club pairs verified by their nearly
identical active first-team rosters and provider/source mappings.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select, text

from data90.config.settings import get_settings
from data90.database.models import (
    Match,
    MatchEvent,
    MatchTeamStatistic,
    PlayerMatchStatistic,
    PlayerTeamSeason,
    SourceTeam,
    StandingRow,
    Team,
    TeamAlias,
    TeamCoach,
    TeamCompetitionSeasonStatistic,
)
from data90.database.session import make_session_factory


# Keep the canonical row with the broader source provenance (football-data.org
# where available); add official display names to a few abbreviated records.
TEAM_MAP = {
    147: 85,   # Olympique Lyon -> Olympique Lyonnais
    143: 112,  # NEC Nijmegen -> NEC
    148: 81,   # Olympique Marseille -> Olympique de Marseille
    157: 62,   # TSG Hoffenheim -> TSG 1899 Hoffenheim
    129: 107,  # AZ Alkmaar -> AZ
    155: 88,   # Stade Rennes -> Stade Rennais FC 1901
    139: 49,   # Juventus Turin -> Juventus FC
    151: 32,   # Real Sociedad -> Real Sociedad de Fútbol
    132: 37,   # Celta Vigo -> RC Celta de Vigo
    128: 41,   # AC Mailand -> AC Milan
}
DISPLAY_NAMES = {107: "AZ Alkmaar", 112: "NEC Nijmegen"}
MIN_SHARED_ROSTER = {128: 4}  # AC Mailand is the German alias of AC Milan; smaller provider roster.
UNIQUE_CHILDREN = (
    (TeamAlias, ("source_id", "normalized_alias")),
    (TeamCoach, ("source_id",)),
    (PlayerTeamSeason, ("player_id", "competition_season_id", "source_id")),
    (MatchTeamStatistic, ("match_id", "source_id")),
    (TeamCompetitionSeasonStatistic, ("competition_season_id", "source_id")),
    (StandingRow, ("snapshot_id",)),
)


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def backup_rows(session: Any) -> Path:
    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"club-dedup-{stamp}.json.gz"
    ids = set(TEAM_MAP) | set(TEAM_MAP.values())
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "database": get_settings().database_name,
        "team_map": TEAM_MAP,
        "tables": {},
    }
    for model in (
        Team,
        TeamAlias,
        TeamCoach,
        SourceTeam,
        PlayerTeamSeason,
        Match,
        MatchTeamStatistic,
        TeamCompetitionSeasonStatistic,
        PlayerMatchStatistic,
        MatchEvent,
        StandingRow,
    ):
        predicates = []
        for column in model.__table__.columns:
            if column.name in {"team_id", "home_team_id", "away_team_id", "winner_team_id"}:
                predicates.append(column.in_(ids))
        if predicates:
            from sqlalchemy import or_

            rows = session.scalars(select(model).where(or_(*predicates))).all()
            payload["tables"][model.__tablename__] = [_row_dict(row) for row in rows]
    with gzip.open(destination, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, default=_json_default)
    return destination


def _merge_child_rows(session: Any, model: Any, keys: tuple[str, ...], conflicts: Counter[str]) -> int:
    ids = set(TEAM_MAP) | set(TEAM_MAP.values())
    rows = session.scalars(select(model).where(model.team_id.in_(ids))).all()
    groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in rows:
        target = TEAM_MAP.get(row.team_id, row.team_id)
        groups[(target, *(getattr(row, key) for key in keys))].append(row)

    deleted = 0
    for key, group in groups.items():
        target = key[0]
        if len(group) == 1:
            group[0].team_id = target
            continue

        def richness(row: Any) -> tuple[int, datetime, int]:
            filled = sum(
                getattr(row, column.name) is not None
                for column in row.__table__.columns
                if column.name not in {"id", "team_id", *keys}
            )
            updated = getattr(row, "source_updated_at", None) or datetime.min.replace(tzinfo=UTC)
            return filled, updated, row.id

        winner = max(group, key=richness)
        for row in group:
            if row is winner:
                continue
            for column in row.__table__.columns:
                name = column.name
                if name in {"id", "team_id", *keys}:
                    continue
                current, other = getattr(winner, name), getattr(row, name)
                if current is None and other is not None:
                    setattr(winner, name, other)
                elif current is not None and other is not None and current != other:
                    conflicts[f"{model.__tablename__}.{name}"] += 1
            session.delete(row)
            deleted += 1
        winner.team_id = target
    session.flush()
    return deleted


def _remap_nonunique_references(session: Any) -> None:
    for duplicate_id, canonical_id in TEAM_MAP.items():
        for attribute in ("home_team_id", "away_team_id", "winner_team_id"):
            if hasattr(Match, attribute):
                session.query(Match).filter(getattr(Match, attribute) == duplicate_id).update(
                    {attribute: canonical_id}, synchronize_session=False
                )
        for model in (SourceTeam, PlayerMatchStatistic, MatchEvent):
            session.query(model).filter(model.team_id == duplicate_id).update(
                {model.team_id: canonical_id}, synchronize_session=False
            )
    session.flush()


def run(apply: bool) -> None:
    factory = make_session_factory()
    with factory() as session, session.begin():
        # Validate all ten expected club pairs and the >20-player evidence that
        # made each match safe to consolidate.
        for duplicate_id, canonical_id in TEAM_MAP.items():
            duplicate, canonical = session.get(Team, duplicate_id), session.get(Team, canonical_id)
            if duplicate is None or canonical is None:
                raise RuntimeError(f"Falta el club esperado {duplicate_id}->{canonical_id}.")
            if duplicate.country_name and canonical.country_name and duplicate.country_name != canonical.country_name:
                raise RuntimeError(f"País incompatible en {duplicate.canonical_name}/{canonical.canonical_name}.")
            duplicate_players = set(session.scalars(select(PlayerTeamSeason.player_id).where(
                PlayerTeamSeason.team_id == duplicate_id, PlayerTeamSeason.active.is_(True)
            )))
            canonical_players = set(session.scalars(select(PlayerTeamSeason.player_id).where(
                PlayerTeamSeason.team_id == canonical_id, PlayerTeamSeason.active.is_(True)
            )))
            shared = len(duplicate_players & canonical_players)
            minimum_shared = MIN_SHARED_ROSTER.get(duplicate_id, 20)
            if shared < minimum_shared:
                raise RuntimeError(f"Solapamiento insuficiente ({shared}) en {duplicate.canonical_name}/{canonical.canonical_name}.")
            print(f"{duplicate.canonical_name} ({duplicate_id}) -> {canonical.canonical_name} ({canonical_id}); plantel compartido: {shared}")

        # No merge may turn a fixture into a club playing itself.
        for duplicate_id, canonical_id in TEAM_MAP.items():
            self_fixture = session.scalar(select(Match.id).where(
                ((Match.home_team_id == duplicate_id) & (Match.away_team_id == canonical_id))
                | ((Match.home_team_id == canonical_id) & (Match.away_team_id == duplicate_id))
            ).limit(1))
            if self_fixture is not None:
                raise RuntimeError(f"El merge {duplicate_id}->{canonical_id} crearía un partido contra sí mismo: {self_fixture}.")

        if not apply:
            print("Simulación solamente; no se modificó ningún dato.")
            return

        print(f"Respaldo creado: {backup_rows(session)}")
        conflict_counts: Counter[str] = Counter()
        removed = {}
        for model, keys in UNIQUE_CHILDREN:
            removed[model.__tablename__] = _merge_child_rows(session, model, keys, conflict_counts)
        _remap_nonunique_references(session)

        for duplicate_id, canonical_id in TEAM_MAP.items():
            canonical, duplicate = session.get(Team, canonical_id), session.get(Team, duplicate_id)
            if canonical.country_name is None:
                canonical.country_name = duplicate.country_name
            for field in ("city", "venue_name", "founded_year", "crest_url", "short_code"):
                if getattr(canonical, field) is None and getattr(duplicate, field) is not None:
                    setattr(canonical, field, getattr(duplicate, field))
            if canonical_id in DISPLAY_NAMES:
                canonical.canonical_name = DISPLAY_NAMES[canonical_id]
            canonical.active = canonical.active or duplicate.active
            canonical.updated_at = datetime.now(UTC)
            session.delete(duplicate)
        session.flush()

        inspector = inspect(session.bind)
        for table_name in inspector.get_table_names():
            for foreign_key in inspector.get_foreign_keys(table_name):
                if foreign_key.get("referred_table") != "teams":
                    continue
                column = foreign_key["constrained_columns"][0]
                for duplicate_id in TEAM_MAP:
                    remaining = session.execute(
                        text(f'SELECT count(*) FROM "{table_name}" WHERE "{column}" = :team_id'),
                        {"team_id": duplicate_id},
                    ).scalar_one()
                    if remaining:
                        raise RuntimeError(f"Quedaron referencias al club {duplicate_id} en {table_name}.{column}.")

        print("Filas consolidadas por tabla:", dict(removed))
        print("Campos conflictivos preservados en respaldo:", dict(conflict_counts))
        print("Referencias a clubes duplicados: 0; todos los clubes duplicados fueron retirados.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="aplica la consolidación tras crear respaldo")
    run(parser.parse_args().apply)
