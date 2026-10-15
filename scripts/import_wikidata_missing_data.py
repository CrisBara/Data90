"""Fill safe, missing player bios and club coaches from Wikidata's CC0 data."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import requests
from sqlalchemy import select

from data90.database.session import make_engine, make_session_factory
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Player,
    PlayerTeamSeason,
    Match,
    Season,
    SourcePlayer,
    SourceTeam,
    SyncRun,
    SyncRunItem,
    Team,
    TeamAlias,
    TeamCoach,
)
from data90.domain.enums import SyncMode, SyncStatus
from data90.repositories.players import team_squad_for_team
from data90.sync.normalizer import normalize_name


SOURCE_CODE = "wikidata"
SOURCE_URL = "https://www.wikidata.org"
API_URL = "https://www.wikidata.org/w/api.php"
TARGET_CODES = ("PL", "LALIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "EREDIVISIE", "UCL", "UEL")
SOCCER_PLAYER = "Q937857"
FOOTBALL_CLUB = "Q476028"
TEAM_SUFFIXES = {"fc", "afc", "cf", "sc", "ac", "club", "football", "the"}


def _key(value: str) -> str:
    words = normalize_name(value).split()
    while words and words[-1] in TEAM_SUFFIXES:
        words.pop()
    return " ".join(words)


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


class Wikidata:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "Data90/1.0 (desktop football data enrichment)"}
        )
        self.requests = 0
        self._last_request = 0.0

    def get(self, params: dict[str, Any]) -> dict[str, Any]:
        delay = 0.35 - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        response = self.session.get(API_URL, params=params, timeout=(10, 35))
        self._last_request = time.monotonic()
        self.requests += 1
        if response.status_code == 429:
            time.sleep(max(5, int(response.headers.get("Retry-After", "5"))))
            response = self.session.get(API_URL, params=params, timeout=(10, 35))
            self._last_request = time.monotonic()
            self.requests += 1
        response.raise_for_status()
        return response.json()

    def search(self, term: str) -> list[dict[str, Any]]:
        data = self.get(
            {
                "action": "wbsearchentities",
                "search": term,
                "language": "en",
                "type": "item",
                "limit": 50,
                "format": "json",
            }
        )
        key = normalize_name(term)
        rows = data.get("search", [])
        exact: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = row.get("label") or ""
            match_text = (row.get("match") or {}).get("text") or ""
            if normalize_name(label) == key or normalize_name(match_text) == key:
                qid = row.get("id")
                if isinstance(qid, str) and qid.startswith("Q"):
                    exact[qid] = row
        return list(exact.values())

    def entities(self, qids: set[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        ordered = sorted(qids)
        for offset in range(0, len(ordered), 50):
            data = self.get(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(ordered[offset : offset + 50]),
                    "props": "claims|labels|info",
                    "languages": "es|en|fr|de|it|nl",
                    "languagefallback": 1,
                    "format": "json",
                }
            )
            out.update(data.get("entities", {}))
        return out


def _item_id(statement: dict[str, Any]) -> str | None:
    value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
    return value.get("id") if isinstance(value, dict) else None


def _claims(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    return entity.get("claims", {}).get(prop, [])


def _time_claim(statement: dict[str, Any], prop: str) -> date | None:
    for qualifier in statement.get("qualifiers", {}).get(prop, []):
        value = qualifier.get("datavalue", {}).get("value", {})
        raw = value.get("time") if isinstance(value, dict) else None
        precision = value.get("precision") if isinstance(value, dict) else None
        if not isinstance(raw, str) or not isinstance(precision, int):
            continue
        if precision < 9:
            continue
        match = re.match(r"^[+-]?(\d{4,})-(\d{2})-(\d{2})", raw)
        if not match:
            continue
        year, month, day = map(int, match.groups())
        if precision == 9:
            month, day = 1, 1
        elif precision == 10:
            day = 1
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _current_statements(entity: dict[str, Any], prop: str, today: date) -> list[dict[str, Any]]:
    out = []
    for statement in _claims(entity, prop):
        if statement.get("rank") == "deprecated":
            continue
        starts = _time_claim(statement, "P580")
        ends = _time_claim(statement, "P582")
        if starts and starts > today:
            continue
        if ends and ends < today:
            continue
        out.append(statement)
    preferred = [row for row in out if row.get("rank") == "preferred"]
    return preferred or out


def _label(entity: dict[str, Any]) -> str | None:
    labels = entity.get("labels", {})
    for language in ("es", "en", "fr", "de", "it", "nl"):
        row = labels.get(language)
        if isinstance(row, dict) and row.get("value"):
            return str(row["value"])
    return None


def _item_ids(entity: dict[str, Any], prop: str) -> set[str]:
    return {qid for row in _claims(entity, prop) if (qid := _item_id(row))}


def _birthdate(entity: dict[str, Any]) -> date | None:
    values = []
    for row in _claims(entity, "P569"):
        value = row.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if not isinstance(value, dict) or value.get("precision") != 11:
            continue
        raw = value.get("time")
        if isinstance(raw, str):
            try:
                values.append(date.fromisoformat(raw.lstrip("+")[:10]))
            except ValueError:
                pass
    distinct = set(values)
    return next(iter(distinct)) if len(distinct) == 1 else None


def _position_ids(entity: dict[str, Any]) -> set[str]:
    return _item_ids(entity, "P413")


def _nationality_ids(entity: dict[str, Any]) -> set[str]:
    sporting = _item_ids(entity, "P1532")
    if len(sporting) == 1:
        return sporting
    citizenship = _item_ids(entity, "P27")
    return citizenship if len(citizenship) == 1 else set()


def _sporting_clubs(entity: dict[str, Any], today: date) -> set[str]:
    return {
        qid
        for statement in _current_statements(entity, "P54", today)
        if (qid := _item_id(statement))
    }


def _competitions_and_squads(
    session: Any,
    competition_codes: tuple[str, ...] | None = None,
) -> tuple[dict[int, set[str]], dict[int, set[int]], dict[int, dict[str, Any]], set[int]]:
    player_competitions: dict[int, set[str]] = defaultdict(set)
    player_teams: dict[int, set[int]] = defaultdict(set)
    player_rows: dict[int, dict[str, Any]] = {}
    target_team_ids: set[int] = set()
    target_codes = competition_codes if competition_codes is not None else TARGET_CODES
    for code in target_codes:
        competition = session.scalar(select(Competition).where(Competition.code == code))
        if competition is None:
            continue
        competition_season = session.scalar(
            select(CompetitionSeason)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .where(
                CompetitionSeason.competition_id == competition.id,
                Season.active.is_(True),
            )
            .order_by(Season.start_year.desc())
            .limit(1)
        )
        if competition_season is None:
            continue
        team_ids = set(
            session.scalars(
                select(PlayerTeamSeason.team_id)
                .where(
                    PlayerTeamSeason.competition_season_id == competition_season.id,
                    PlayerTeamSeason.active.is_(True),
                )
                .distinct()
            ).all()
        )
        match_team_ids = set(
            session.scalars(select(Match.home_team_id).where(Match.competition_season_id == competition_season.id)).all()
        ) | set(
            session.scalars(select(Match.away_team_id).where(Match.competition_season_id == competition_season.id)).all()
        )
        target_team_ids.update(team_ids)
        target_team_ids.update(match_team_ids)
        for team_id in team_ids:
            for member in team_squad_for_team(session, team_id):
                player = session.get(Player, member.player_id)
                if player is None:
                    continue
                player_competitions[player.id].add(code)
                player_teams[player.id].add(team_id)
                row = player_rows.setdefault(
                    player.id,
                    {
                        "name": player.display_name,
                        "nationality": player.nationality or member.nationality,
                        "birth_date": player.birth_date or member.birth_date,
                        "position": player.position or member.position,
                    },
                )
                row["nationality"] = row["nationality"] or member.nationality
                row["birth_date"] = row["birth_date"] or member.birth_date
                row["position"] = row["position"] or member.position
    return player_competitions, player_teams, player_rows, target_team_ids


def _team_name_keys(session: Any, team_ids: set[int]) -> dict[int, set[str]]:
    names: dict[int, set[str]] = defaultdict(set)
    for team in session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
        for value in (team.canonical_name, team.short_name):
            if value:
                names[team.id].add(_key(value))
    for alias in session.scalars(select(TeamAlias).where(TeamAlias.team_id.in_(team_ids))).all():
        names[alias.team_id].add(_key(alias.alias))
    return names


def _entity_is_player(entity: dict[str, Any]) -> bool:
    return SOCCER_PLAYER in _item_ids(entity, "P106")


def _eligible_coach(entity: dict[str, Any], today: date) -> str | None:
    rows = _current_statements(entity, "P286", today)
    ids = {qid for row in rows if (qid := _item_id(row))}
    return next(iter(ids)) if len(ids) == 1 else None


def _clubs_and_coaches(
    api: Wikidata, session: Any, missing_team_ids: set[int]
) -> tuple[dict[int, str], dict[str, dict[str, Any]], dict[int, str]]:
    names_by_team: dict[int, list[str]] = {}
    for team in session.scalars(select(Team).where(Team.id.in_(missing_team_ids))).all():
        values = [team.canonical_name]
        if team.short_name:
            values.append(team.short_name)
        for alias in session.scalars(select(TeamAlias.alias).where(TeamAlias.team_id == team.id)):
            values.append(alias)
        names_by_team[team.id] = list(dict.fromkeys(v for v in values if v))

    qids_by_team: dict[int, set[str]] = defaultdict(set)
    teams_by_qid: dict[str, set[int]] = defaultdict(set)
    seen_terms: set[str] = set()
    team_terms: list[tuple[int, str]] = []
    for team_id, values in names_by_team.items():
        for value in values:
            key = normalize_name(value)
            if key and key not in seen_terms:
                seen_terms.add(key)
                team_terms.append((team_id, value))

    for index, (team_id, value) in enumerate(team_terms, 1):
        try:
            for row in api.search(value):
                qid = row.get("id")
                if isinstance(qid, str):
                    description = (row.get("description") or "").lower()
                    if not any(
                        marker in description
                        for marker in ("football club", "soccer club", "association football club")
                    ):
                        continue
                    qids_by_team[team_id].add(qid)
                    teams_by_qid[qid].add(team_id)
        except Exception as exc:
            print(f"Wikidata team search failed for {value}: {exc.__class__.__name__}")
        if index % 25 == 0:
            print(f"Wikidata clubs searched: {index}/{len(team_terms)}", flush=True)

    entities = api.entities(set(teams_by_qid))
    team_qid_for_id: dict[int, str] = {}
    qids_for_team: dict[int, set[str]] = defaultdict(set)
    coach_qids: set[str] = set()
    today = date.today()
    for qid, local_teams in teams_by_qid.items():
        entity = entities.get(qid, {})
        if FOOTBALL_CLUB not in _item_ids(entity, "P31"):
            continue
        if len(local_teams) != 1:
            continue
        team_id = next(iter(local_teams))
        # Search APIs can return an exact alias for a different entity; require
        # the Wikidata label itself to correspond to a stored team name.
        label = _label(entity)
        if not label or _key(label) not in names_by_team.get(team_id, set()):
            # Non-Spanish local labels can be represented by the exact alias
            # that the search matched; the shared alias index remains exact.
            possible_keys = {_key(v) for v in names_by_team.get(team_id, set())}
            if not label or _key(label) not in possible_keys:
                continue
        qids_for_team[team_id].add(qid)
        coach_qid = _eligible_coach(entity, today)
        if coach_qid:
            coach_qids.add(coach_qid)

    # A club is used only when its team identity resolved to one Wikidata item.
    for team_id, qids in qids_for_team.items():
        if len(qids) == 1:
            team_qid_for_id[team_id] = next(iter(qids))

    # Return current preferred (or unique current) managers for uniquely mapped teams.
    coach_by_team: dict[int, str] = {}
    for team_id, team_qid in team_qid_for_id.items():
        entity = entities[team_qid]
        coach_qid = _eligible_coach(entity, today)
        if coach_qid:
            coach_by_team[team_id] = coach_qid
    return coach_by_team, api.entities(coach_qids), team_qid_for_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    parser.add_argument("--competitions", nargs="+", choices=TARGET_CODES)
    args = parser.parse_args()
    api = Wikidata()
    engine = make_engine()
    session_factory = make_session_factory(engine)
    now = datetime.now(UTC)
    try:
        with session_factory.begin() as session:
            source = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
            if source is None:
                source = DataSource(
                    code=SOURCE_CODE,
                    name="Wikidata",
                    base_url=SOURCE_URL,
                    priority=9,
                    enabled=True,
                    requires_api_key=False,
                    license_name="CC0 1.0 (structured data)",
                    attribution_text="Datos estructurados de Wikidata",
                    terms_url="https://www.wikidata.org/wiki/Wikidata:Licensing",
                )
                session.add(source)
                session.flush()
            source_id = source.id
            run = SyncRun(
                mode=SyncMode.MANUAL.value,
                status=SyncStatus.RUNNING.value,
                started_at=now,
            )
            session.add(run)
            session.flush()
            run_id: UUID = run.id

        with session_factory() as session:
            target_codes = tuple(args.competitions) if args.competitions else TARGET_CODES
            player_competitions, player_teams, player_rows, team_ids = _competitions_and_squads(
                session, target_codes
            )
            team_names = _team_name_keys(session, team_ids)
            existing_team_coaches = set(
                session.scalars(select(TeamCoach.team_id).where(TeamCoach.team_id.in_(team_ids))).all()
            ) if team_ids else set()
            missing_team_ids = team_ids - existing_team_coaches
            existing_players_by_name: dict[str, set[int]] = defaultdict(set)
            targets: dict[int, dict[str, Any]] = {}
            for player_id, row in player_rows.items():
                missing = any(row[field] is None for field in ("nationality", "birth_date", "position"))
                if not missing:
                    continue
                key = normalize_name(row["name"])
                existing_players_by_name[key].add(player_id)
                targets[player_id] = row
            unique_names: dict[str, str] = {}
            for player_id, row in targets.items():
                key = normalize_name(row["name"])
                if len(existing_players_by_name[key]) == 1:
                    unique_names[key] = row["name"]
            local_teams_by_player = {
                player_id: player_teams.get(player_id, set()) for player_id in targets
            }

        # Resolve club items and coach statements first; this also provides the
        # exact local team-name index used to reject conflicting player club links.
        with session_factory() as session:
            coach_by_team, coach_entities, team_qid_for_id = _clubs_and_coaches(
                api, session, missing_team_ids
            )
        print(
            f"Wikidata: {len(unique_names)} unique player names to search; "
            f"{len(missing_team_ids)} clubs without a recorded coach.",
            flush=True,
        )

        qids_by_name: dict[str, set[str]] = defaultdict(set)
        for index, (name_key, name) in enumerate(unique_names.items(), 1):
            try:
                for row in api.search(name):
                    qid = row.get("id")
                    if isinstance(qid, str):
                        qids_by_name[name_key].add(qid)
            except Exception as exc:
                print(f"Wikidata player search failed for {name}: {exc.__class__.__name__}")
            if index % 50 == 0:
                print(f"Wikidata player names searched: {index}/{len(unique_names)}", flush=True)

        all_player_qids = set().union(*qids_by_name.values()) if qids_by_name else set()
        player_entities = api.entities(all_player_qids)
        qid_to_player: dict[str, int] = {}
        ambiguous_names = 0
        for name_key, qids in qids_by_name.items():
            local_ids = existing_players_by_name.get(name_key, set())
            footballers = [
                qid
                for qid in qids
                if qid in player_entities and _entity_is_player(player_entities[qid])
            ]
            if len(local_ids) != 1 or len(footballers) != 1:
                ambiguous_names += int(bool(qids))
                continue
            qid_to_player[footballers[0]] = next(iter(local_ids))

        # Retrieve country, position and current-club labels in a single batched pass.
        detail_ids: set[str] = set(coach_entities)
        detail_ids.update(qid_to_player)
        for qid in qid_to_player:
            entity = player_entities[qid]
            detail_ids.update(_nationality_ids(entity))
            detail_ids.update(_position_ids(entity))
            detail_ids.update(_sporting_clubs(entity, date.today()))
        details = api.entities(detail_ids)

        club_label_by_qid = {
            qid: _label(details.get(qid, {}))
            for qid in detail_ids
            if _label(details.get(qid, {}))
        }

        plans: dict[int, dict[str, Any]] = {}
        skipped_club_conflict = 0
        for qid, player_id in qid_to_player.items():
            entity = player_entities[qid]
            local_team_ids = local_teams_by_player.get(player_id, set())
            local_keys = set().union(*(team_names.get(tid, set()) for tid in local_team_ids)) if local_team_ids else set()
            current_club_qids = _sporting_clubs(entity, date.today())
            current_club_labels = {club_label_by_qid.get(club) for club in current_club_qids}
            current_club_labels.discard(None)
            if current_club_labels and local_keys:
                if not any(_key(label) in local_keys for label in current_club_labels):
                    skipped_club_conflict += 1
                    continue
            values: dict[str, Any] = {
                "nationality": None,
                "birth_date": _birthdate(entity),
                "position": None,
            }
            nation_ids = _nationality_ids(entity)
            if len(nation_ids) == 1:
                values["nationality"] = _label(details.get(next(iter(nation_ids)), {}))
            pos_ids = _position_ids(entity)
            pos_labels = sorted(
                {
                    label
                    for pos_id in pos_ids
                    if (label := _label(details.get(pos_id, {})))
                }
            )
            if pos_labels:
                values["position"] = " / ".join(pos_labels[:3])
            if any(values.values()):
                plans[player_id] = {"qid": qid, "values": values}

        before: dict[str, Any] = {"players": [], "coaches": []}
        fields_filled = defaultdict(int)
        changed_players: set[int] = set()
        changed_coaches: set[int] = set()
        skipped_identity = 0
        with session_factory.begin() as session:
            source = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
            source_id = source.id
            for player_id, plan in plans.items():
                player = session.get(Player, player_id)
                if player is None:
                    continue
                identity = session.scalar(
                    select(SourcePlayer).where(
                        SourcePlayer.source_id == source_id,
                        SourcePlayer.external_id == plan["qid"],
                    )
                )
                if identity is not None and identity.player_id != player_id:
                    skipped_identity += 1
                    continue
                if identity is None:
                    session.add(
                        SourcePlayer(source_id=source_id, external_id=plan["qid"], player_id=player_id)
                    )
                changed = False
                before_row = {"player_id": player_id, "name": player.display_name}
                for field, value in plan["values"].items():
                    if getattr(player, field) is None and value is not None:
                        before_row[field] = None
                        setattr(player, field, value)
                        fields_filled[field] += 1
                        changed = True
                if changed:
                    player.profile_source_id = source_id
                    player.profile_updated_at = now
                    changed_players.add(player_id)
                    before["players"].append(before_row)

            for team_id, coach_qid in coach_by_team.items():
                if team_id in existing_team_coaches:
                    continue
                # Find the uniquely resolved club QID again from the local map.
                team_qid = team_qid_for_id.get(team_id)
                if not team_qid:
                    continue
                linked = session.scalar(
                    select(SourceTeam).where(
                        SourceTeam.source_id == source_id,
                        SourceTeam.external_id == team_qid,
                    )
                )
                if linked is not None and linked.team_id != team_id:
                    continue
                if linked is None:
                    session.add(
                        SourceTeam(source_id=source_id, external_id=team_qid, team_id=team_id)
                    )
                coach_name = _label(coach_entities.get(coach_qid, {}))
                if not coach_name:
                    continue
                coach_row = session.scalar(
                    select(TeamCoach).where(
                        TeamCoach.team_id == team_id,
                        TeamCoach.source_id == source_id,
                    )
                )
                if coach_row is None:
                    session.add(
                        TeamCoach(
                            team_id=team_id,
                            source_id=source_id,
                            external_id=coach_qid,
                            full_name=coach_name,
                        )
                    )
                    changed_coaches.add(team_id)

        # Persist a compact rollback aid containing only the changed profile fields.
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = args.backup_dir / f"wikidata-import-{now:%Y%m%dT%H%M%SZ}.json"
        backup.write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")

        per_code_players: dict[str, set[int]] = defaultdict(set)
        for player_id in changed_players:
            for code in player_competitions.get(player_id, set()):
                per_code_players[code].add(player_id)
        with session_factory.begin() as session:
            source = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source.id,
                    competition_id=None,
                    operation="WIKIDATA_MISSING_PROFILES_AND_COACHES",
                    status=SyncStatus.SUCCESS.value,
                    requests_count=api.requests,
                    inserted_count=0,
                    updated_count=len(changed_players) + len(changed_coaches),
                    skipped_count=ambiguous_names + skipped_club_conflict + skipped_identity,
                    error_count=0,
                    message=(
                        f"Perfiles completados por competición: "
                        + ", ".join(f"{code}={len(per_code_players.get(code, set()))}" for code in target_codes)
                        + f"; entrenadores nuevos={len(changed_coaches)}"
                    ),
                )
            )
            run = session.get(SyncRun, run_id)
            run.finished_at = datetime.now(UTC)
            run.status = SyncStatus.SUCCESS.value
            run.requests_count = api.requests
            run.updated_count = len(changed_players) + len(changed_coaches)
            run.inserted_count = 0
            run.skipped_count = ambiguous_names + skipped_club_conflict + skipped_identity
            run.error_count = 0
            run.summary = (
                "Wikidata CC0: perfiles biográficos y entrenadores actuales; "
                "sin importar estadísticas de temporada"
            )

        print(f"SyncRun: {run_id}")
        print(f"Solicitudes Wikidata: {api.requests}; nombres únicos examinados: {len(unique_names)}")
        print(f"Perfiles enlazados con datos: {len(plans)}; perfiles modificados: {len(changed_players)}")
        print(
            "Campos completados: "
            + ", ".join(f"{field}={count}" for field, count in fields_filled.items())
        )
        print(f"Entrenadores nuevos: {len(changed_coaches)}")
        print(
            f"Omitidos por ambigüedad={ambiguous_names}, club incompatible={skipped_club_conflict}, "
            f"identidad vinculada a otro jugador={skipped_identity}"
        )
        print(f"Respaldo de cambios: {backup.resolve()}")
        return 0
    except Exception:
        # Do not leave a misleading RUNNING record if a remote lookup or local
        # transaction fails. Earlier verified imports remain independently atomic.
        with session_factory.begin() as session:
            active = session.get(SyncRun, run_id) if "run_id" in locals() else None
            if active is not None:
                active.finished_at = datetime.now(UTC)
                active.status = SyncStatus.FAILED.value
                active.summary = "Wikidata: la sincronización se interrumpió antes de completar la importación"
        raise
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
