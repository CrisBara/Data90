"""Read-only, responsive web companion for Data 90.

The desktop application remains the primary client. This WSGI app exposes the
same SQLAlchemy services through a same-origin JSON API and serves the browser UI.
All API requests use read-only PostgreSQL transactions; there are no write routes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Engine

from data90.config.featured_match_priority import (
    FEATURED_COMPETITION_PRIORITY,
    featured_match_key,
    normalize_team_name,
)
from data90.config.settings import get_settings
from data90.config.team_crests import verified_team_crest_url
from data90.database.session import make_engine, make_session_factory
from data90.domain.exceptions import EntityResolutionError
from data90.repositories.global_search import search_teams_and_players
from data90.repositories.players import (
    player_profile_detail,
    player_position_label,
    player_roster_for_competition_season,
    player_statistics_for_competition_season,
    scorer_snapshot_player_ids,
)
from data90.services.competition_service import CompetitionService
from data90.services.featured_match_policy import featured_match_policy
from data90.services.match_service import MatchService
from data90.services.statistics_service import StatisticsService
from data90.services.team_service import TeamService
from data90.sync.normalizer import normalize_name

LOG = logging.getLogger("data90.web")
WEB_ROOT = Path(__file__).parent / "ui" / "web"
COMPETITION_LOGOS = {
    "PL": "https://crests.football-data.org/PL.png",
    "LALIGA": "https://crests.football-data.org/PD.png",
    "SERIE_A": "https://crests.football-data.org/SA.png",
    "BUNDESLIGA": "https://crests.football-data.org/BL1.png",
    "LIGUE_1": "https://crests.football-data.org/FL1.png",
    "EREDIVISIE": (
        "https://eredivisie.b-cdn.net/production/pages/"
        "VriendenLoterij_Eredivisie_LOGO_HOR_DIAP.png?height=240&quality=85"
    ),
    "UCL": "https://crests.football-data.org/CL.png",
    "UEL": "https://crests.football-data.org/EL.png",
}
TEAM_RIVALRY_CODES = {"PL", "LALIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "EREDIVISIE"}


def _jsonable(value):
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "_asdict"):
        return _jsonable(value._asdict())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return str(value)


_CREST_NAME_KEYS = ("name", "team_name", "home_team", "away_team", "opponent", "rival_name")
_CREST_URL_KEYS = (
    "crest",
    "crest_url",
    "home_crest_url",
    "away_crest_url",
    "opponent_crest_url",
    "rival_crest_url",
)


def _apply_verified_crests(value):
    """Replace known bad or missing provider URLs without guessing team identities."""
    if isinstance(value, list):
        return [_apply_verified_crests(item) for item in value]
    if isinstance(value, dict):
        result = {key: _apply_verified_crests(item) for key, item in value.items()}
        names = [result.get(key) for key in _CREST_NAME_KEYS if isinstance(result.get(key), str)]
        for crest_key in _CREST_URL_KEYS:
            if crest_key in result:
                for name in names:
                    verified = verified_team_crest_url(name, result[crest_key])
                    if verified != result[crest_key]:
                        result[crest_key] = verified
                        break
        return result
    return value


def _stage(match) -> str:
    if match.stage:
        label = " ".join(str(match.stage).replace("_", " ").split())
        if label.casefold() == "regular season":
            return f"Jornada {match.matchday}" if match.matchday else "Partido"
        return label
    return f"Jornada {match.matchday}" if match.matchday else "Partido"


def _match_payload(
    match,
    *,
    competition_code: str | None = None,
    competition_name: str | None = None,
    season_label: str | None = None,
) -> dict[str, object]:
    return {
        "id": getattr(match, "match_id", None),
        "competition_code": getattr(match, "competition_code", competition_code),
        "competition_name": getattr(match, "competition_name", competition_name),
        "season": getattr(match, "season_label", season_label),
        "kickoff": match.kickoff_utc,
        "stage": _stage(match),
        "round_label": getattr(match, "round_label", None),
        "matchday": match.matchday,
        "home": {
            "id": match.home_team_id,
            "name": match.home_team,
            "crest": match.home_crest_url,
        },
        "away": {
            "id": match.away_team_id,
            "name": match.away_team,
            "crest": match.away_crest_url,
        },
        "status": match.status,
        "home_score": match.home_score_ft,
        "away_score": match.away_score_ft,
    }


def _competition_payload(item) -> dict[str, object]:
    return {
        "id": item.competition_id,
        "code": item.code,
        "name": item.name,
        "country": item.country_name or item.region_name,
        "type": item.competition_type,
        "season": item.latest_season,
        "teams": item.team_count,
        "matches": item.match_count,
        "last_synced_at": item.last_synced_at,
        "logo": COMPETITION_LOGOS.get(item.code),
    }


def _get_competition(session, code: str):
    service = CompetitionService(session)
    competition = next((item for item in service.get_competitions() if item.code == code), None)
    if competition is None:
        return None
    return service, competition


def _select_season(service, code: str, requested: str | None):
    seasons = service.get_seasons(code)
    if requested:
        try:
            season_id = int(requested)
        except ValueError:
            season_id = -1
        selected = next((item for item in seasons if item.competition_season_id == season_id), None)
        if selected:
            return seasons, selected
    return seasons, seasons[0] if seasons else None


def _home_data(session, selected: date) -> dict[str, object]:
    settings = get_settings()
    priority_policy = featured_match_policy(session)
    service = CompetitionService(session)
    competitions = service.get_competitions()
    by_code = {item.code: item for item in competitions}
    ordered = [by_code[code] for code in FEATURED_COMPETITION_PRIORITY if code in by_code]
    team_positions: dict[str, dict[str, tuple[int, int]]] = {}
    for competition in ordered:
        if (competition.competition_type or "").upper() != "LEAGUE":
            continue
        seasons = service.get_seasons(competition.code)
        if not seasons:
            continue
        rows = service.get_standings(seasons[0].competition_season_id)
        if rows:
            team_positions[competition.code] = {
                normalize_team_name(row.team_name): (row.position, len(rows)) for row in rows
            }

    days = []
    today_matches = []
    for offset in range(6):
        day = selected + timedelta(days=offset)
        matches = MatchService(session, settings.app_timezone).get_matches(day)
        matches = [
            match for match in matches if match.competition_code in FEATURED_COMPETITION_PRIORITY
        ]
        if offset == 0:
            today_matches = matches
        groups = []
        for competition in ordered:
            items = [match for match in matches if match.competition_code == competition.code]
            if not items:
                continue
            items.sort(key=lambda item: item.kickoff_utc)
            featured = min(
                items,
                key=lambda item: featured_match_key(
                    item, team_positions.get(competition.code, {}), priority_policy
                ),
            )
            groups.append(
                {
                    "competition": _competition_payload(competition),
                    "matches": [_match_payload(item) for item in items],
                    "featured": _match_payload(featured),
                }
            )
        highlighted = min(
            matches,
            key=lambda item: featured_match_key(
                item, team_positions.get(item.competition_code, {}), priority_policy
            ),
            default=None,
        )
        days.append(
            {
                "date": day.isoformat(),
                "match_count": len(matches),
                "featured": _match_payload(highlighted) if highlighted else None,
                "competitions": groups,
            }
        )
    return {
        "today": selected.isoformat(),
        "timezone": settings.app_timezone,
        "days": days,
        "today_matches": [_match_payload(item) for item in today_matches],
        "competitions": [_competition_payload(item) for item in ordered],
    }


def _competition_detail(session, code: str, requested_season: str | None):
    found = _get_competition(session, code)
    if found is None:
        return None
    service, competition = found
    seasons, season = _select_season(service, code, requested_season)
    if season is None:
        return {
            "competition": _competition_payload(competition),
            "seasons": _jsonable(seasons),
            "season": None,
            "standings": [],
            "matches": [],
            "team_statistics": [],
            "scorers": [],
            "knockouts": [],
        }

    matches = service.get_matches(season.competition_season_id)
    standings = service.get_standings(season.competition_season_id)
    statistics_service = StatisticsService(session)
    team_statistics = statistics_service.for_competition_season(season.competition_season_id)
    match_statistics = statistics_service.match_stat_summaries_for_competition_season(
        season.competition_season_id
    )
    player_stats = player_statistics_for_competition_season(session, season.competition_season_id)
    roster = player_roster_for_competition_season(session, season.competition_season_id)
    roster_by_player = defaultdict(list)
    for member in roster:
        roster_by_player[member.player_id].append(member)

    snapshot_ids = (
        scorer_snapshot_player_ids(session, season.competition_season_id, "fotmob")
        if code in {"BUNDESLIGA", "EREDIVISIE", "LIGUE_1", "SERIE_A", "LALIGA"}
        else set()
    )
    scorers = _competition_scorers(player_stats, roster_by_player, snapshot_ids)

    team_stats = []
    for item in team_statistics:
        metric = _jsonable(item.metrics)
        match_summary = _jsonable(match_statistics.get(item.team_id))
        aggregate = item.promiedos
        team_stats.append(
            {
                "team_id": item.team_id,
                "team_name": item.team_name,
                "crest_url": item.crest_url,
                "metrics": metric,
                "event_statistics": match_summary,
                "published_aggregate": (
                    {
                        "goals_average": aggregate.goals_average,
                        "goals_conceded_average": aggregate.goals_conceded_average,
                        "clean_sheets": aggregate.clean_sheets,
                        "possession_percentage": aggregate.possession_percentage,
                        "corners_average": aggregate.corners_average,
                        "penalties_scored": aggregate.penalties_scored,
                        "penalties_attempted": aggregate.penalties_attempted,
                        "penalties_conceded": aggregate.penalties_conceded,
                        "yellow_cards": aggregate.yellow_cards,
                        "red_cards": aggregate.red_cards,
                    }
                    if aggregate
                    else None
                ),
            }
        )

    return {
        "competition": _competition_payload(competition),
        "seasons": _jsonable(seasons),
        "season": _jsonable(season),
        "standings": _jsonable(standings),
        "matches": [
            _match_payload(
                item,
                competition_code=competition.code,
                competition_name=competition.name,
                season_label=season.label,
            )
            for item in matches
        ],
        "team_statistics": team_stats,
        "scorers": scorers,
        "knockouts": [
            _match_payload(
                item,
                competition_code=competition.code,
                competition_name=competition.name,
                season_label=season.label,
            )
            for item in matches
            if _is_knockout_match(item)
        ],
    }


def _competition_scorers(player_stats, roster_by_player, snapshot_ids: set[int]) -> list[dict]:
    """Apply the same provider merge and club choice as the desktop scorers tab."""
    if snapshot_ids:
        snapshot_targets: dict[int, list[tuple[int, str]]] = {}
        for player in player_stats:
            if player.player_id not in snapshot_ids or player.source_code != "fotmob":
                continue
            name_key = normalize_name(player.display_name)
            for member in roster_by_player.get(player.player_id, []):
                if member.source_code == "fotmob":
                    snapshot_targets.setdefault(member.team_id, []).append(
                        (player.player_id, name_key)
                    )

        supplemental_by_target: dict[int, dict[int, list[object]]] = {}
        for player in player_stats:
            if player.player_id in snapshot_ids or player.source_code == "fotmob":
                continue
            if player.assists is None:
                continue
            matching_targets = set()
            for member in roster_by_player.get(player.player_id, []):
                for target_id, target_name in snapshot_targets.get(member.team_id, []):
                    if _same_player_name_variant(player.display_name, target_name):
                        matching_targets.add(target_id)
            if len(matching_targets) == 1:
                target_id = next(iter(matching_targets))
                supplemental_by_target.setdefault(target_id, {}).setdefault(
                    player.assists, []
                ).append(player)

        supplemental = []
        for target_id, assist_values in supplemental_by_target.items():
            if len(assist_values) == 1:
                player = next(iter(assist_values.values()))[0]
                supplemental.append(
                    replace(player, player_id=target_id, goals=None, appearances=None)
                )
        player_stats = [
            player
            for player in player_stats
            if player.player_id in snapshot_ids
            and (player.source_code != "fotmob" or player.goals is not None)
        ] + supplemental

    merged: dict[int, dict[str, object]] = {}
    for player in player_stats:
        values = merged.setdefault(
            player.player_id,
            {
                "name": player.display_name,
                "goals": None,
                "assists": None,
                "appearances": None,
                "appearance_source": None,
                "source": player.source_code,
            },
        )
        if player.player_id in snapshot_ids and player.source_code == "fotmob":
            values["name"] = player.display_name
        for field in ("goals", "assists", "appearances"):
            value = getattr(player, field)
            if field == "goals" and player.player_id in snapshot_ids:
                if player.source_code == "fotmob":
                    values[field] = value
                    values["source"] = player.source_code
                    continue
                if values["source"] == "fotmob":
                    continue
            if field == "appearances" and value is not None:
                if values[field] is None or value > values[field]:
                    values[field] = value
                    values["appearance_source"] = player.source_code
                continue
            if value is not None and (values[field] is None or value > values[field]):
                values[field] = value
                if field == "appearances":
                    values["appearance_source"] = player.source_code
                if field == "goals":
                    values["source"] = player.source_code

    output = []
    ordered = sorted(
        (
            (player_id, item)
            for player_id, item in merged.items()
            if item["goals"] is not None and item["goals"] > 0
        ),
        key=lambda item: (-item[1]["goals"], str(item[1]["name"]).casefold()),
    )
    for player_id, item in ordered:
        members = roster_by_player.get(player_id, [])
        member = next(
            (entry for entry in members if entry.source_code == item["source"]),
            members[0] if members else None,
        )
        output.append(
            {
                "id": player_id,
                "name": item["name"],
                "goals": item["goals"],
                "assists": item["assists"],
                "appearances": item["appearances"],
                "team": (
                    {"id": member.team_id, "name": member.team_name, "crest": member.crest_url}
                    if member
                    else None
                ),
            }
        )
    return output


def _same_player_name_variant(left: str, right: str) -> bool:
    left_tokens = normalize_name(left).split()
    right_tokens = normalize_name(right).split()
    if left_tokens == right_tokens:
        return True
    shorter, longer = sorted((left_tokens, right_tokens), key=len)
    if len(shorter) >= 2:
        index = 0
        for token in longer:
            if token == shorter[index]:
                index += 1
                if index == len(shorter):
                    return True
    return (
        len(left_tokens) >= 2
        and len(right_tokens) >= 2
        and left_tokens[-1] == right_tokens[-1]
        and (
            left_tokens[0] == right_tokens[0]
            or (len(left_tokens[0]) == 1 and right_tokens[0].startswith(left_tokens[0]))
            or (len(right_tokens[0]) == 1 and left_tokens[0].startswith(right_tokens[0]))
        )
    )


def _is_knockout_match(match) -> bool:
    phase = (
        " ".join(str(value or "") for value in (match.stage, getattr(match, "round_label", None)))
        .casefold()
        .replace("_", " ")
    )
    phase = re.sub(r"[–—-]", " ", phase)
    markers = (
        "knockout",
        "play off",
        "playoff",
        "round of",
        "round-of",
        "last 16",
        "last 8",
        "quarter final",
        "semi final",
        "final",
        "octavos",
        "dieciseisavos",
        "cuartos",
        "semifinal",
        "repechaje",
        "eliminatoria",
        "1/8",
        "1/4",
        "1/16",
    )
    return any(marker in phase for marker in markers)


def _team_detail(session, team_id: int, requested_season: str | None):
    service = TeamService(session)
    try:
        profile = service.get_profile(team_id, int(requested_season) if requested_season else None)
    except (ValueError, LookupError, EntityResolutionError):
        return None
    selected = next(
        (
            item
            for item in profile.competitions
            if item.competition_season_id == profile.selected_competition_season_id
        ),
        None,
    )
    rivalries = (
        service.get_rivalry_sections(team_id, selected.competition_code)
        if selected and selected.competition_code in TEAM_RIVALRY_CODES
        else ()
    )
    squad = _jsonable(profile.squad)
    for player in squad:
        player["position"] = player_position_label(player.get("position"))
    return {
        "team": {
            "id": profile.team_id,
            "name": profile.name,
            "crest": profile.crest_url,
            "country": profile.country_name,
            "coach": profile.coach_name,
        },
        "competitions": _jsonable(profile.competitions),
        "selected_season_id": profile.selected_competition_season_id,
        "selected_competition_code": selected.competition_code if selected else None,
        "standing": {"position": profile.standing_position, "points": profile.standing_points},
        "metrics": _jsonable(profile.metrics),
        "recent_matches": _jsonable(profile.recent_matches),
        "upcoming_matches": _jsonable(profile.upcoming_matches),
        "match_history": _jsonable(profile.match_history),
        "match_statistics": _jsonable(profile.match_stat_averages),
        "match_stat_totals": _jsonable(profile.stat_totals),
        "squad": squad,
        "rivalries": _jsonable(rivalries),
    }


def _player_detail(session, player_id: int):
    profile = player_profile_detail(session, player_id)
    if profile is None:
        return None
    age = None
    if profile.birth_date:
        today = datetime.now(UTC).date()
        age = (
            today.year
            - profile.birth_date.year
            - ((today.month, today.day) < (profile.birth_date.month, profile.birth_date.day))
        )
    payload = _jsonable(profile)
    payload["position"] = player_position_label(profile.position)
    for membership in payload.get("team_competitions", []):
        membership["position"] = player_position_label(membership.get("position"))
    payload["age"] = age
    return payload


class FootballAnalyticsWeb:
    """Small same-origin WSGI service with a read-only JSON API."""

    def __init__(
        self,
        session_factory=None,
        engine: Engine | None = None,
        *,
        enable_sync: bool = True,
    ) -> None:
        self._factory = session_factory
        self._engine = engine
        self._sync_job = None
        if enable_sync:
            from data90.services.sync_job import SyncJob

            self._sync_job = SyncJob()

    def _sessions(self):
        if self._factory is None:
            database_url = get_settings().data90_web_database_url.get_secret_value().strip()
            self._engine = make_engine(url=database_url) if database_url else make_engine()
            self._factory = make_session_factory(self._engine)
        return self._factory

    @staticmethod
    def _reply(start_response, status: HTTPStatus, body: bytes, content_type: str):
        start_response(
            f"{status.value} {status.phrase}",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                ("X-Frame-Options", "DENY"),
                ("Referrer-Policy", "no-referrer"),
                (
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' https: data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'self'; frame-ancestors 'none'",
                ),
            ],
        )
        return [body]

    def _dispatch_api(self, path: str, query: dict[str, list[str]]):
        factory = self._sessions()
        with factory() as session:
            if session.bind and session.bind.dialect.name == "postgresql":
                session.connection().exec_driver_sql("SET TRANSACTION READ ONLY")
            if path == "/api/health":
                session.execute(text("SELECT 1"))
                return {"status": "ok", "database": "connected", "read_only": True}
            if path == "/api/competitions":
                items = CompetitionService(session).get_competitions()
                return [_competition_payload(item) for item in items]
            if path == "/api/home":
                timezone = ZoneInfo(get_settings().app_timezone)
                today = datetime.now(timezone).date()
                requested = query.get("date", [today.isoformat()])[0]
                try:
                    selected = date.fromisoformat(requested)
                except ValueError:
                    raise ValueError("La fecha debe usar formato AAAA-MM-DD") from None
                return _home_data(session, selected)
            if path == "/api/search":
                term = query.get("q", [""])[0].strip()
                if len(term) > 120:
                    raise ValueError("La búsqueda es demasiado larga")
                return _jsonable(search_teams_and_players(session, term, limit=80))

            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "competition"]:
                requested = query.get("season", [None])[0]
                result = _competition_detail(session, parts[2], requested)
                if result is None:
                    raise KeyError("Competición no encontrada")
                return result
            if len(parts) == 3 and parts[:2] == ["api", "team"]:
                try:
                    team_id = int(parts[2])
                except ValueError:
                    raise ValueError("Identificador de equipo inválido") from None
                requested = query.get("season", [None])[0]
                result = _team_detail(session, team_id, requested)
                if result is None:
                    raise KeyError("Equipo no encontrado")
                return result
            if len(parts) == 3 and parts[:2] == ["api", "player"]:
                try:
                    player_id = int(parts[2])
                except ValueError:
                    raise ValueError("Identificador de jugador inválido") from None
                result = _player_detail(session, player_id)
                if result is None:
                    raise KeyError("Jugador no encontrado")
                return result
        raise KeyError("Ruta de API no encontrada")

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if method not in {"GET", "POST"}:
            return self._reply(
                start_response,
                HTTPStatus.METHOD_NOT_ALLOWED,
                b"Solo lectura",
                "text/plain; charset=utf-8",
            )
        allowed_hosts = {
            value.strip().casefold()
            for value in get_settings().data90_web_allowed_hosts.split(",")
            if value.strip()
        }
        raw_host = environ.get("HTTP_HOST", "localhost").casefold()
        request_host = (
            raw_host[1 : raw_host.find("]")]
            if raw_host.startswith("[")
            else (raw_host.rsplit(":", 1)[0] if raw_host.count(":") == 1 else raw_host)
        )
        if request_host not in allowed_hosts:
            return self._reply(
                start_response,
                HTTPStatus.BAD_REQUEST,
                b"Host no permitido",
                "text/plain; charset=utf-8",
            )
        path = environ.get("PATH_INFO", "/")
        if path == "/api/sync/status" and method == "GET":
            payload = json.dumps(
                self._sync_job.snapshot() if self._sync_job else {"running": False},
                ensure_ascii=False,
            ).encode("utf-8")
            return self._reply(
                start_response, HTTPStatus.OK, payload, "application/json; charset=utf-8"
            )
        if path == "/api/sync" and method == "POST":
            if self._sync_job is None:
                return self._reply(
                    start_response,
                    HTTPStatus.NOT_FOUND,
                    b"No encontrado",
                    "text/plain; charset=utf-8",
                )
            origin = environ.get("HTTP_ORIGIN", "")
            host = environ.get("HTTP_HOST", "").casefold()
            try:
                origin_host = origin.split("://", 1)[1].split("/", 1)[0].casefold()
            except IndexError:
                origin_host = ""
            if not origin_host or origin_host != host:
                return self._reply(
                    start_response,
                    HTTPStatus.FORBIDDEN,
                    json.dumps({"error": "Solicitud de actualización no válida"}).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            try:
                payload = json.dumps(self._sync_job.start(), ensure_ascii=False).encode("utf-8")
                return self._reply(
                    start_response, HTTPStatus.ACCEPTED, payload, "application/json; charset=utf-8"
                )
            except RuntimeError as error:
                payload = json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8")
                return self._reply(
                    start_response, HTTPStatus.CONFLICT, payload, "application/json; charset=utf-8"
                )
        if path.startswith("/api/"):
            if method != "GET":
                return self._reply(
                    start_response,
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    b"Metodo no permitido",
                    "text/plain; charset=utf-8",
                )
            try:
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                result = self._dispatch_api(path, query)
                body = json.dumps(
                    _apply_verified_crests(_jsonable(result)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                return self._reply(
                    start_response, HTTPStatus.OK, body, "application/json; charset=utf-8"
                )
            except ValueError as error:
                return self._reply(
                    start_response,
                    HTTPStatus.BAD_REQUEST,
                    json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            except KeyError as error:
                return self._reply(
                    start_response,
                    HTTPStatus.NOT_FOUND,
                    json.dumps({"error": str(error.args[0])}, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )
            except Exception as error:
                LOG.exception("Web API request failed (%s)", error.__class__.__name__)
                body = json.dumps(
                    {"error": "No se pudo consultar la aplicación"}, ensure_ascii=False
                ).encode("utf-8")
                return self._reply(
                    start_response,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    body,
                    "application/json; charset=utf-8",
                )

        asset_name = "index.html" if path in {"", "/"} else path.removeprefix("/assets/")
        if path != "/" and not path.startswith("/assets/"):
            return self._reply(
                start_response, HTTPStatus.NOT_FOUND, b"No encontrado", "text/plain; charset=utf-8"
            )
        if not re.fullmatch(r"[A-Za-z0-9._-]+", asset_name):
            return self._reply(
                start_response, HTTPStatus.NOT_FOUND, b"No encontrado", "text/plain; charset=utf-8"
            )
        asset = (WEB_ROOT / asset_name).resolve()
        if asset.parent != WEB_ROOT.resolve() or not asset.is_file():
            return self._reply(
                start_response, HTTPStatus.NOT_FOUND, b"No encontrado", "text/plain; charset=utf-8"
            )
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(asset.suffix, "application/octet-stream")
        try:
            return self._reply(start_response, HTTPStatus.OK, asset.read_bytes(), content_type)
        except OSError:
            return self._reply(
                start_response, HTTPStatus.NOT_FOUND, b"No encontrado", "text/plain; charset=utf-8"
            )


application = FootballAnalyticsWeb()


def main() -> int:
    from wsgiref.simple_server import make_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    host = settings.data90_web_host.strip()
    port = settings.data90_web_port
    if host not in {"127.0.0.1", "localhost", "::1"}:
        if (
            not settings.data90_web_allow_remote
            or not settings.data90_web_database_url.get_secret_value()
        ):
            raise SystemExit(
                "El acceso remoto requiere DATA90_WEB_ALLOW_REMOTE=1 y una DATA90_WEB_DATABASE_URL "
                "con un usuario PostgreSQL de solo lectura."
            )
    with make_server(host, port, application) as server:
        LOG.info("Data 90 web escuchando en http://%s:%s", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            LOG.info("Servidor detenido")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
