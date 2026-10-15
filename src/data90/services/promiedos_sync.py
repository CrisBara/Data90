"""Sync current player leaderboards and standings from Promiedos."""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from data90.database.models import (
    Competition,
    DataSource,
    Match,
    PlayerTeamSeason,
    SourceTeam,
    StandingRow,
    StandingsSnapshot,
    SyncRun,
    SyncRunItem,
    Team,
    TeamAlias,
)
from data90.domain.dto import (
    NormalizedPlayerSeasonStatistic,
    NormalizedSeason,
    NormalizedStanding,
)
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderError
from data90.providers.promiedos import PromiedosProvider
from data90.repositories.catalog import upsert_competition_season
from data90.repositories.players import upsert_player_statistic
from data90.repositories.standings import add_standings_snapshot
from data90.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]

PROMIEDOS_TEAM_NAME_EQUIVALENTS = {
    "aek atenas": "pae aek",
    "ac milan": "ac mailand",
    "atletico de madrid": "club atletico de madrid",
    "bodo glimt": "fk bod glimt",
    "brujas": "club brugge kv",
    "celta de vigo": "rc celta de vigo",
    "estrasburgo": "rc strasbourg alsace",
    "friburgo": "sc freiburg",
    "hamburgo sv": "hamburger sv",
    "hapoel beer sheva": "hapoel beer scheva",
    "hoffenheim": "tsg 1899 hoffenheim",
    "lens": "racing club de lens",
    "lyon": "olympique lyonnais",
    "lillestrom": "lillestr m sk",
    "marsella": "olympique de marseille",
    "n e c nijmegen": "nec nijmegen",
    "niza": "ogc nice",
    "olympiacos": "olympiakos",
    "olympique marsella": "olympique marseille",
    "racing de santander": "real racing club de santander",
    "real sociedad": "real sociedad de futbol",
    "red bull salzburgo": "rb salzburg",
    "rennes": "stade rennais fc 1901",
    "shakhtar donetsk": "fk shakhtar donetsk",
    "slavia praga": "sk slavia praha",
    "slovan bratislava": "k slovan bratislava",
    "viktoria plzen": "viktoria pilsen",
    "werder bremen": "sv werder bremen",
}

PROMIEDOS_LEAGUE_IDS = {
    "PL": "h",
    "LALIGA": "bb",
    "SERIE_A": "bh",
    "BUNDESLIGA": "cf",
    "LIGUE_1": "df",
    "UCL": "fhc",
    "UEL": "fhd",
}

STATISTIC_FIELDS = {
    "goles": "goals",
    "asistencias": "assists",
    "tarjetas amarillas": "yellow_cards",
    "tarjetas rojas": "red_cards",
    "penales convertidos": "penalties_scored",
    "barridas ganadas": "tackles_won",
    "partidos jugados": "appearances",
    "titularidades": "starts",
    "apariciones como suplente": "substitute_appearances",
    "remates": "shots_total",
    "tiros": "shots_total",
    "remates al arco": "shots_on_target",
    "tiros al arco": "shots_on_target",
    "vallas invictas": "clean_sheets",
    "goles recibidos": "goals_conceded",
}


@dataclass(frozen=True, slots=True)
class PromiedosSyncSummary:
    run_id: UUID | None
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _team_index(session: Session) -> dict[str, set[int]]:
    names: dict[str, set[int]] = defaultdict(set)
    for team in session.scalars(select(Team)).all():
        key = normalize_name(team.canonical_name)
        if key:
            names[key].add(team.id)
    for normalized_alias, team_id in session.execute(
        select(TeamAlias.normalized_alias, TeamAlias.team_id)
    ):
        if normalized_alias:
            names[normalized_alias].add(team_id)
    return names


def _int_value(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        match = re.match(r"\s*(-?\d+)", value)
        if match:
            return int(match.group(1))
    return None


def _rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    for group in data.get("tables_groups", []):
        if not isinstance(group, dict):
            continue
        for table in group.get("tables", []):
            if not isinstance(table, dict):
                continue
            rows = table.get("table", {}).get("rows", [])
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
    return []


def _value_map(row: dict[str, Any]) -> dict[str, Any]:
    values = row.get("values")
    if not isinstance(values, list):
        return {}
    return {
        str(item.get("key")): item.get("value")
        for item in values
        if isinstance(item, dict) and item.get("key") is not None
    }


def _team_source_mappings(
    session: Session,
    source_id: int,
    competition_season_id: int,
    team_index: dict[str, set[int]],
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    allowed_team_ids = {
        team_id
        for pair in session.execute(
            select(Match.home_team_id, Match.away_team_id).where(
                Match.competition_season_id == competition_season_id
            )
        )
        for team_id in pair
    }
    allowed_names: dict[int, set[str]] = defaultdict(set)
    if allowed_team_ids:
        for team in session.scalars(select(Team).where(Team.id.in_(allowed_team_ids))):
            for name in (team.canonical_name, team.short_name):
                if isinstance(name, str) and name.strip():
                    allowed_names[team.id].add(normalize_name(name))
        for alias, team_id in session.execute(
            select(TeamAlias.normalized_alias, TeamAlias.team_id).where(
                TeamAlias.team_id.in_(allowed_team_ids)
            )
        ):
            if alias:
                allowed_names[team_id].add(alias)

    def resolve(labels: tuple[str | None, ...]) -> set[int]:
        keys: set[str] = set()
        for label in labels:
            if not label or not label.strip():
                continue
            key = normalize_name(re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", "", label))
            keys.add(key)
            equivalent = PROMIEDOS_TEAM_NAME_EQUIVALENTS.get(key)
            if equivalent:
                keys.add(normalize_name(equivalent))
        if allowed_names:
            matches = {
                team_id
                for team_id, names in allowed_names.items()
                if any(
                    key == candidate
                    or candidate.startswith(f"{key} ")
                    or key.startswith(f"{candidate} ")
                    for key in keys
                    for candidate in names
                )
            }
            if matches:
                return matches
            for key in keys:
                tokens = set(key.split())
                if len(tokens) < 2:
                    continue
                subset_matches = {
                    team_id
                    for team_id, names in allowed_names.items()
                    if any(tokens.issubset(set(candidate.split())) for candidate in names)
                }
                if len(subset_matches) == 1:
                    return subset_matches
        matches = set()
        for key in keys:
            matches.update(team_index.get(key, set()))
        return matches & allowed_team_ids if allowed_team_ids else matches

    mappings: dict[str, int] = {}
    for row in rows:
        entity = row.get("entity")
        team = entity.get("object") if isinstance(entity, dict) else None
        if not isinstance(team, dict):
            continue
        external_id = team.get("id")
        name = team.get("name")
        if external_id is None or not isinstance(name, str):
            continue
        external_id = str(external_id)
        mapping = session.scalar(
            select(SourceTeam).where(
                SourceTeam.source_id == source_id,
                SourceTeam.external_id == external_id,
            )
        )
        labels = (name, team.get("short_name"))
        candidate_ids = resolve(labels)
        if mapping is not None:
            mapping_names = allowed_names.get(mapping.team_id, set())
            mapping_matches = any(
                normalize_name(label) == candidate
                or candidate.startswith(f"{normalize_name(label)} ")
                or normalize_name(label).startswith(f"{candidate} ")
                for label in labels
                if isinstance(label, str) and label.strip()
                for candidate in mapping_names
            )
            if (not allowed_team_ids or mapping.team_id in allowed_team_ids) and mapping_matches:
                mappings[external_id] = mapping.team_id
                continue
            if len(candidate_ids) != 1:
                continue
            mapping.team_id = next(iter(candidate_ids))
            mappings[external_id] = mapping.team_id
            continue
        if len(candidate_ids) != 1:
            continue
        local_id = next(iter(candidate_ids))
        mapping = SourceTeam(
            source_id=source_id,
            team_id=local_id,
            external_id=external_id,
        )
        session.add(mapping)
        mappings[external_id] = local_id
        for label in (name, team.get("short_name")):
            if not isinstance(label, str) or not label.strip():
                continue
            normalized = normalize_name(label)
            alias = session.scalar(
                select(TeamAlias).where(
                    TeamAlias.source_id == source_id,
                    TeamAlias.normalized_alias == normalized,
                )
            )
            if alias is None:
                session.add(
                    TeamAlias(
                        team_id=local_id,
                        source_id=source_id,
                        alias=label.strip(),
                        normalized_alias=normalized,
                    )
                )
    return mappings


def _player_totals(data: dict[str, Any]) -> dict[tuple[str, str, str | None], dict[str, Any]]:
    totals: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    statistic_data = data.get("players_statistics")
    tables = statistic_data.get("tables", []) if isinstance(statistic_data, dict) else []
    for table in tables:
        if not isinstance(table, dict):
            continue
        field = STATISTIC_FIELDS.get(normalize_name(str(table.get("name", ""))))
        if field is None:
            continue
        for row in table.get("rows", []):
            entity = row.get("entity") if isinstance(row, dict) else None
            player = entity.get("object") if isinstance(entity, dict) else None
            if not isinstance(player, dict):
                continue
            name = player.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            team_id = player.get("team_id")
            team_id = str(team_id) if team_id is not None else ""
            external_id = f"{team_id}:{sha256(normalize_name(name).encode()).hexdigest()[:32]}"
            key = (external_id, name.strip(), team_id or None)
            values = _value_map(row)
            parsed = _int_value(next(iter(values.values()), None))
            if parsed is not None:
                totals.setdefault(key, {})[field] = parsed
            details = totals.setdefault(key, {})
            for source_key, destination in (
                ("position", "position"),
                ("num", "shirt_number"),
                ("birthdate", "birth_date"),
                ("height", "height"),
                ("weight", "weight"),
                ("nationality", "nationality"),
                ("foot", "preferred_foot"),
                ("preferred_foot", "preferred_foot"),
            ):
                value = player.get(source_key)
                if value not in (None, ""):
                    details[destination] = value
    return totals


def _save_competition_data(
    session: Session,
    *,
    source_id: int,
    competition_code: str,
    season_year: int,
    data: dict[str, Any],
    now: datetime,
) -> tuple[int, int, int]:
    season = upsert_competition_season(
        session,
        NormalizedSeason(
            source_code=PromiedosProvider.source_code,
            competition_code=competition_code,
            external_id=str(data["league"].get("id", "")),
            external_code=str(data["league"].get("url_name", "")),
            external_season=str(season_year),
            start_year=season_year,
            end_year=season_year + 1,
        ),
    )
    team_index = _team_index(session)
    all_rows = _rows(data)
    team_ids = _team_source_mappings(session, source_id, season.id, team_index, all_rows)

    standings = []
    for row in all_rows:
        entity = row.get("entity")
        team = entity.get("object") if isinstance(entity, dict) else None
        if not isinstance(team, dict) or team.get("id") is None:
            continue
        values = _value_map(row)
        goal_pair = str(values.get("Goals", "0:0")).split(":", maxsplit=1)
        if len(goal_pair) != 2:
            continue
        goals_for, goals_against = _int_value(goal_pair[0]), _int_value(goal_pair[1])
        if goals_for is None or goals_against is None:
            continue
        standings.append(
            {
                "team_external_id": str(team["id"]),
                "position": _int_value(row.get("num")) or 0,
                "played": _int_value(values.get("GamePlayed")) or 0,
                "won": _int_value(values.get("GamesWon")) or 0,
                "drawn": _int_value(values.get("GamesEven")) or 0,
                "lost": _int_value(values.get("GamesLost")) or 0,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "goal_difference": _int_value(values.get("Ratio")) or 0,
                "points": _int_value(values.get("Points")) or 0,
                "form": None,
            }
        )
    resolved_standings = [
        standing for standing in standings if standing["team_external_id"] in team_ids
    ]
    resolved_team_ids = {team_ids[standing["team_external_id"]] for standing in resolved_standings}
    if (
        standings
        and len(resolved_standings) == len(standings)
        and len(resolved_team_ids) == len(standings)
    ):
        add_standings_snapshot(
            session,
            competition_season_id=season.id,
            source_id=source_id,
            team_ids_by_external_id=team_ids,
            rows=[
                NormalizedStanding(
                    source_code=PromiedosProvider.source_code,
                    competition_code=competition_code,
                    season_start_year=season_year,
                    scope="TOTAL",
                    **standing,
                )
                for standing in resolved_standings
            ],
        )

    # Remove incomplete snapshots produced by earlier alias mappings in this current season.
    if standings:
        snapshots = session.scalars(
            select(StandingsSnapshot).where(
                StandingsSnapshot.source_id == source_id,
                StandingsSnapshot.competition_season_id == season.id,
                StandingsSnapshot.scope == "TOTAL",
            )
        ).all()
        for snapshot in snapshots:
            row_count = session.scalar(
                select(func.count(StandingRow.id)).where(StandingRow.snapshot_id == snapshot.id)
            )
            if row_count != len(standings):
                session.query(StandingRow).filter(StandingRow.snapshot_id == snapshot.id).delete(
                    synchronize_session=False
                )
                session.delete(snapshot)

    totals = _player_totals(data)
    inserted = updated = skipped = 0
    for (external_id, name, external_team_id), values in totals.items():
        statistic = NormalizedPlayerSeasonStatistic(
            source_code=PromiedosProvider.source_code,
            external_id=external_id,
            display_name=name,
            competition_code=competition_code,
            season_start_year=season_year,
            goals=values.get("goals"),
            appearances=values.get("appearances"),
            starts=values.get("starts"),
            substitute_appearances=values.get("substitute_appearances"),
            assists=values.get("assists"),
            yellow_cards=values.get("yellow_cards"),
            red_cards=values.get("red_cards"),
            penalties_scored=values.get("penalties_scored"),
            tackles_won=values.get("tackles_won"),
            shots_total=values.get("shots_total"),
            shots_on_target=values.get("shots_on_target"),
            goals_conceded=values.get("goals_conceded"),
            clean_sheets=values.get("clean_sheets"),
        )
        player, was_inserted, was_updated = upsert_player_statistic(
            session, statistic, competition_season_id=season.id
        )
        nationality = values.get("nationality")
        if isinstance(nationality, str) and nationality.strip():
            player.nationality = nationality.strip()
        birth_text = values.get("birth_date")
        if isinstance(birth_text, str):
            try:
                player.birth_date = date.fromisoformat(birth_text[:10])
            except ValueError:
                pass
        for field in ("height", "weight"):
            value = values.get(field)
            if isinstance(value, str) and value.strip():
                setattr(player, field, value.strip())
        foot = values.get("preferred_foot")
        if isinstance(foot, str) and foot.casefold() in {"left", "right", "both"}:
            player.preferred_foot = foot.upper()
        team_id = team_ids.get(external_team_id or "")
        if team_id is not None:
            memberships = session.scalars(
                select(PlayerTeamSeason).where(
                    PlayerTeamSeason.player_id == player.id,
                    PlayerTeamSeason.competition_season_id == season.id,
                    PlayerTeamSeason.source_id == source_id,
                )
            ).all()
            membership = next(
                (item for item in memberships if item.team_id == team_id),
                memberships[0] if memberships else None,
            )
            for stale_membership in memberships:
                if stale_membership is not membership:
                    session.delete(stale_membership)
            if membership is None:
                membership = PlayerTeamSeason(
                    player_id=player.id,
                    team_id=team_id,
                    competition_season_id=season.id,
                    source_id=source_id,
                )
            else:
                membership.team_id = team_id
            position = values.get("position")
            membership.position = position.strip() if isinstance(position, str) else None
            membership.shirt_number = _int_value(values.get("shirt_number"))
            membership.active = True
            membership.source_updated_at = now
            session.add(membership)
            if isinstance(position, str) and position.strip() and not player.position:
                player.position = position.strip()
            player.profile_source_id = source_id
            player.profile_updated_at = now
        inserted += int(was_inserted)
        updated += int(was_updated and not was_inserted)
        skipped += int(not was_inserted and not was_updated)
    return inserted, updated, skipped


def sync_promiedos_player_statistics(
    session_factory: SessionFactory,
    provider: PromiedosProvider | None = None,
    *,
    now: datetime | None = None,
    competition_codes: tuple[str, ...] | None = None,
) -> PromiedosSyncSummary:
    """Persist today's current tables and player leaderboards from public Promiedos data."""
    provider = provider or PromiedosProvider()
    now = now or datetime.now(UTC)
    season_year = now.astimezone(UTC).year if now.astimezone(UTC).month >= 7 else now.year - 1
    selected = {
        code: external_id
        for code, external_id in PROMIEDOS_LEAGUE_IDS.items()
        if competition_codes is None or code in competition_codes
    }
    with session_factory.begin() as session:
        run = SyncRun(mode=SyncMode.DAILY.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id
        source_id = session.scalar(
            select(DataSource.id).where(DataSource.code == PromiedosProvider.source_code)
        )
        if source_id is None:
            raise ProviderError("Falta Promiedos en el catálogo de fuentes; ejecutá el bootstrap")

    inserted_total = updated_total = skipped_total = errors_total = 0
    errors: list[str] = []
    for code, external_id in selected.items():
        before = provider.request_count
        counts = (0, 0, 0)
        message = None
        status = SyncStatus.SUCCESS.value
        try:
            data = provider.get_league_data(external_id)
            with session_factory.begin() as session:
                counts = _save_competition_data(
                    session,
                    source_id=source_id,
                    competition_code=code,
                    season_year=season_year,
                    data=data,
                    now=now,
                )
        except Exception as error:
            errors_total += 1
            status = SyncStatus.FAILED.value
            message = f"{error.__class__.__name__}: {str(error)[:700]}"
            errors.append(code)
        inserted_total += counts[0]
        updated_total += counts[1]
        skipped_total += counts[2]
        with session_factory.begin() as session:
            competition_id = session.scalar(select(Competition.id).where(Competition.code == code))
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="PROMIEDOS_CURRENT_STATISTICS",
                    status=status,
                    requests_count=provider.request_count - before,
                    inserted_count=counts[0],
                    updated_count=counts[1],
                    skipped_count=counts[2],
                    error_count=int(status == SyncStatus.FAILED.value),
                    error_code="PROMIEDOS_ERROR" if message else None,
                    message=message,
                )
            )

    status = (
        SyncStatus.FAILED
        if errors_total == len(selected)
        else SyncStatus.PARTIAL
        if errors_total
        else SyncStatus.SUCCESS
    )
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        if run is not None:
            run.status = status.value
            run.finished_at = datetime.now(UTC)
            run.requests_count = provider.request_count
            run.inserted_count = inserted_total
            run.updated_count = updated_total
            run.skipped_count = skipped_total
            run.error_count = errors_total
            run.summary = (
                "Estadísticas actuales de Promiedos sincronizadas"
                if not errors
                else f"Competiciones con error: {', '.join(errors)}"
            )
    message = (
        "Tabla, goleadores, asistencias y tarjetas actuales de Promiedos sincronizados"
        if not errors
        else f"Sincronización parcial; revisar: {', '.join(errors)}"
    )
    return PromiedosSyncSummary(
        run_id,
        status,
        provider.request_count,
        inserted_total,
        updated_total,
        skipped_total,
        errors_total,
        message,
    )
