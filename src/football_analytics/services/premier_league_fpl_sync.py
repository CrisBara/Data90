"""Sync Premier League player profiles and season statistics from public FPL data."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Player,
    PlayerCompetitionSeasonStatistic,
    PlayerMatchStatistic,
    PlayerTeamSeason,
    Season,
    Team,
    TeamAlias,
)
from football_analytics.domain.enums import SyncStatus

SessionFactory = Callable[[], Session]
API_URL = "https://fantasy.premierleague.com/api"
SOURCE_CODE = "fpl-premier-league"
POSITION_BY_ELEMENT_TYPE = {
    1: "Portero",
    2: "Defensor",
    3: "Mediocampista",
    4: "Delantero",
}
FPL_TEAM_ALIASES = {
    "Brighton": ("Brighton Hove Albion FC",),
    "Leeds": ("Leeds United FC",),
    "Man City": ("Manchester City FC",),
    "Man Utd": ("Manchester United FC",),
    "Newcastle": ("Newcastle United FC",),
    "Nott'm Forest": ("Nottingham Forest FC",),
    "Spurs": ("Tottenham Hotspur FC",),
    "Bournemouth": ("AFC Bournemouth",),
}


@dataclass(frozen=True, slots=True)
class PremierLeagueFplSyncSummary:
    status: SyncStatus
    requests_count: int
    updated_count: int
    error_count: int
    unmatched_names: tuple[str, ...]
    message: str


def _normalise(value: str) -> str:
    value = value.replace("ß", "ss").replace("ø", "o").replace("Ø", "O")
    value = value.replace("�", "").replace("�", "")
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]", "", plain)


def _normalise_team(value: str) -> str:
    key = _normalise(value)
    for suffix in ("footballclub", "afc", "fc", "cf", "sc", "club"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return key


def _parse_profile_date(value: object):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).date()
    except ValueError:
        return None


def _player_matches(name: str, elements: list[dict]) -> list[dict]:
    key = _normalise(name)
    exact = [
        player
        for player in elements
        if _normalise(f"{player['first_name']} {player['second_name']}") == key
    ]
    if exact:
        return exact
    web_names = [player for player in elements if _normalise(player["web_name"]) == key]
    if web_names:
        return web_names
    tokens = name.strip().split()
    if len(tokens) >= 2:
        first, last = tokens[0].rstrip("."), tokens[-1].rstrip(".")
        first_matches = [
            player
            for player in elements
            if _first_name_matches(first, player["first_name"])
            and (
                _normalise(last) == _normalise(player["web_name"])
                or _normalise(last) in _normalise(f"{player['second_name']} {player['web_name']}")
                or _normalise(name) in _normalise(f"{player['first_name']} {player['second_name']}")
            )
        ]
        if first_matches:
            return first_matches
    # Some local providers invert their abbreviated display format ("Cherki R.").
    inverted_initial = re.fullmatch(r"\s*([^.]*)\s+([A-Za-z])\.\s*", name)
    if inverted_initial:
        web_name, initial = inverted_initial.groups()
        return [
            player
            for player in elements
            if _normalise(player["web_name"]) == _normalise(web_name)
            and player["first_name"][:1].casefold() == initial.casefold()
        ]
    initial_last = re.fullmatch(r"\s*([A-Za-z])\.\s*(.+?)\s*", name)
    if initial_last:
        initial, last = initial_last.groups()
        return [
            player
            for player in elements
            if player["first_name"][:1].casefold() == initial.casefold()
            and _normalise(player["second_name"]) == _normalise(last)
        ]
    return []


def _first_name_matches(local_first: str, provider_first: str) -> bool:
    local_key = _normalise(local_first)
    provider_tokens = [_normalise(token) for token in provider_first.split()]
    aliases = {"chema": "josemaria", "joshua": "josh"}
    local_key = aliases.get(local_key, local_key)
    if local_key in _normalise(provider_first):
        return True
    if any(local_key == token or local_key in token for token in provider_tokens):
        return True
    return any(
        (
            (local_key.endswith("a") and local_key[:-1] == token)
            or (
                min(len(local_key), len(token)) >= 4
                and SequenceMatcher(None, local_key, token).ratio() >= 0.86
            )
        )
        for token in provider_tokens
    )


def sync_premier_league_fpl_appearances(
    session_factory: SessionFactory,
    *,
    session: requests.Session | None = None,
) -> PremierLeagueFplSyncSummary:
    """Sync current PL roster profiles and season totals from FPL's public endpoints.

    Bootstrap provides season totals for every listed player. One live endpoint per
    completed gameweek supplies appearances and starts without a request per player.
    Local records are linked by team and a unique player-name match; ambiguous records
    are left unchanged.
    """
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", "FootballAnalyticsPro/1.0")
    request_count = updated = errors = 0
    unmatched: list[str] = []
    try:
        response = http.get(f"{API_URL}/bootstrap-static/", timeout=25)
        request_count += 1
        response.raise_for_status()
        bootstrap = response.json()
        elements = bootstrap.get("elements", [])
        teams = bootstrap.get("teams", [])
        completed_events = [event for event in bootstrap.get("events", []) if event.get("finished")]
    except (requests.RequestException, ValueError, TypeError) as error:
        return PremierLeagueFplSyncSummary(
            SyncStatus.FAILED, request_count, 0, 1, (), f"FPL bootstrap: {error}"
        )

    with session_factory.begin() as db:
        season_row = db.execute(
            select(CompetitionSeason.id, Season.label)
            .join(Competition, Competition.id == CompetitionSeason.competition_id)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .where(Competition.code == "PL")
            .order_by(Season.start_year.desc())
            .limit(1)
        ).first()
        if season_row is None:
            return PremierLeagueFplSyncSummary(
                SyncStatus.FAILED, request_count, 0, 1, (), "No hay temporada PL en la base local."
            )
        competition_season_id, season_label = season_row
        source = db.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
        if source is None:
            source = DataSource(
                code=SOURCE_CODE,
                name="Premier League / Fantasy Premier League",
                base_url=API_URL,
                priority=1,
                requires_api_key=False,
                license_name="Feed público de Fantasy Premier League",
                attribution_text="Participaciones contrastadas con Fantasy Premier League",
                terms_url="https://fantasy.premierleague.com/",
            )
            db.add(source)
            db.flush()
        source_id = source.id
        roster_rows = db.execute(
            select(
                PlayerTeamSeason.player_id,
                PlayerTeamSeason.team_id,
                Player.display_name,
            )
            .join(Player, Player.id == PlayerTeamSeason.player_id)
            .where(
                PlayerTeamSeason.competition_season_id == competition_season_id,
                PlayerTeamSeason.active.is_(True),
            )
            .distinct()
        ).all()
        local_team_names: dict[int, set[str]] = {}
        local_names_by_team: dict[int, dict[str, set[int]]] = {}
        for player_id, team_id, name in roster_rows:
            local_names_by_team.setdefault(team_id, {}).setdefault(_normalise(name), set()).add(
                player_id
            )
        team_rows = db.execute(
            select(Team.id, Team.canonical_name, Team.short_name)
            .join(PlayerTeamSeason, PlayerTeamSeason.team_id == Team.id)
            .where(
                PlayerTeamSeason.competition_season_id == competition_season_id,
                PlayerTeamSeason.active.is_(True),
            )
            .distinct()
        ).all()
        for team_id, canonical_name, short_name in team_rows:
            local_team_names.setdefault(team_id, set()).update(
                value for value in (canonical_name, short_name) if value
            )
        for team_id, alias in db.execute(
            select(TeamAlias.team_id, TeamAlias.alias)
            .join(PlayerTeamSeason, PlayerTeamSeason.team_id == TeamAlias.team_id)
            .where(
                PlayerTeamSeason.competition_season_id == competition_season_id,
                PlayerTeamSeason.active.is_(True),
            )
            .distinct()
        ):
            local_team_names.setdefault(team_id, set()).add(alias)

        fpl_teams_by_id = {int(team["id"]): team for team in teams}
        local_team_by_name: dict[str, set[int]] = {}
        for team_id, names in local_team_names.items():
            for name in names:
                local_team_by_name.setdefault(_normalise_team(name), set()).add(team_id)
        for fpl_name, local_names in FPL_TEAM_ALIASES.items():
            fpl_team_ids = [
                int(team["id"])
                for team in teams
                if _normalise_team(team["name"]) == _normalise_team(fpl_name)
            ]
            for fpl_team_id in fpl_team_ids:
                matched_ids = {
                    team_id
                    for local_name in local_names
                    for team_id in local_team_by_name.get(_normalise_team(local_name), set())
                }
                if len(matched_ids) == 1:
                    fpl_teams_by_id[fpl_team_id]["_local_team_id"] = next(iter(matched_ids))
        for fpl_team in teams:
            if "_local_team_id" in fpl_teams_by_id[int(fpl_team["id"])]:
                continue
            candidates = local_team_by_name.get(_normalise_team(fpl_team["name"]), set())
            if len(candidates) == 1:
                fpl_teams_by_id[int(fpl_team["id"])]["_local_team_id"] = next(iter(candidates))

        elements_by_team: dict[int, list[dict]] = {}
        for element in elements:
            if element.get("removed"):
                continue
            team = fpl_teams_by_id.get(int(element.get("team") or 0))
            local_team_id = team.get("_local_team_id") if team else None
            if local_team_id is not None:
                elements_by_team.setdefault(local_team_id, []).append(element)
        player_elements: dict[int, dict] = {}
        for team_id, name_groups in local_names_by_team.items():
            club_elements = elements_by_team.get(team_id, [])
            claimed: dict[int, str] = {}
            for normalized_name, player_ids in name_groups.items():
                display_name = next(
                    name
                    for candidate_id, candidate_team_id, name in roster_rows
                    if candidate_team_id == team_id and candidate_id in player_ids
                )
                matches = _player_matches(display_name, club_elements)
                if len(matches) != 1:
                    # Some provider rosters lag behind transfers. A globally unique
                    # full-name match still identifies the player safely; the season
                    # totals remain Premier League totals, independent of club.
                    exact_matches = [
                        element
                        for element in elements
                        if _normalise(f"{element['first_name']} {element['second_name']}")
                        == normalized_name
                    ]
                    if len(exact_matches) == 1:
                        matches = exact_matches
                if len(matches) != 1:
                    unmatched.append(display_name)
                    continue
                element = matches[0]
                external_id = int(element["id"])
                if external_id in claimed and claimed[external_id] != normalized_name:
                    unmatched.append(display_name)
                    continue
                claimed[external_id] = normalized_name
                for player_id in player_ids:
                    player_elements[player_id] = element

    # The live endpoint returns one compact round of statistics for every player.
    # Only completed rounds are included, so in-progress matches cannot create partial totals.
    appearances_by_element: dict[int, int] = {}
    starts_by_element: dict[int, int] = {}
    live_rounds_ok = 0
    for event in completed_events:
        try:
            response = http.get(f"{API_URL}/event/{event['id']}/live/", timeout=25)
            request_count += 1
            response.raise_for_status()
            live_elements = response.json().get("elements", [])
            for live in live_elements:
                element_id = int(live.get("id") or 0)
                stats = live.get("stats") if isinstance(live.get("stats"), dict) else {}
                if int(stats.get("minutes") or 0) > 0:
                    appearances_by_element[element_id] = (
                        appearances_by_element.get(element_id, 0) + 1
                    )
                starts_by_element[element_id] = starts_by_element.get(element_id, 0) + int(
                    stats.get("starts") or 0
                )
            live_rounds_ok += 1
        except (requests.RequestException, ValueError, TypeError, KeyError):
            errors += 1
    now = datetime.now(UTC)
    with session_factory.begin() as db:
        source = db.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
        source_id = source.id if source is not None else source_id
        detailed_appearances_by_player: dict[int, set[int]] = {}
        detailed_starts_by_player: dict[int, set[int]] = {}
        for player_id, match_id, is_substitute in db.execute(
            select(
                PlayerMatchStatistic.player_id,
                PlayerMatchStatistic.match_id,
                PlayerMatchStatistic.is_substitute,
            )
            .join(Match, Match.id == PlayerMatchStatistic.match_id)
            .where(
                Match.competition_season_id == competition_season_id,
                Match.status == "FINISHED",
                PlayerMatchStatistic.minutes_played > 0,
            )
        ):
            detailed_appearances_by_player.setdefault(player_id, set()).add(match_id)
            if is_substitute is False:
                detailed_starts_by_player.setdefault(player_id, set()).add(match_id)
        for player_id, element in player_elements.items():
            element_id = int(element["id"])
            statistics = db.scalar(
                select(PlayerCompetitionSeasonStatistic).where(
                    PlayerCompetitionSeasonStatistic.player_id == player_id,
                    PlayerCompetitionSeasonStatistic.competition_season_id == competition_season_id,
                    PlayerCompetitionSeasonStatistic.source_id == source_id,
                )
            )
            if statistics is None:
                statistics = PlayerCompetitionSeasonStatistic(
                    player_id=player_id,
                    competition_season_id=competition_season_id,
                    source_id=source_id,
                )
            aggregate_fields = {
                "minutes_played": "minutes",
                "goals": "goals_scored",
                "assists": "assists",
                "yellow_cards": "yellow_cards",
                "red_cards": "red_cards",
                "goals_conceded": "goals_conceded",
                "clean_sheets": "clean_sheets",
                "shots_total": "total_shots",
                "shots_on_target": "shots_on_target",
            }
            for target, field in aggregate_fields.items():
                value = element.get(field)
                if isinstance(value, int) and value >= 0:
                    setattr(statistics, target, value)
            statistics.source_updated_at = now
            if live_rounds_ok == len(completed_events):
                appearances = appearances_by_element.get(element_id, 0)
                starts = starts_by_element.get(element_id, 0)
                # The FPL live feed can omit an appearance even when its season
                # totals include those minutes. Verified match rows provide a
                # lower bound for appearances and starts, not other season totals.
                appearances = max(
                    appearances, len(detailed_appearances_by_player.get(player_id, set()))
                )
                starts = max(starts, len(detailed_starts_by_player.get(player_id, set())))
                statistics.appearances = appearances
                statistics.starts = starts
                statistics.substitute_appearances = max(appearances - starts, 0)
            else:
                # Preserve missing counts for players with minutes if a live round failed.
                if not int(element.get("minutes") or 0):
                    statistics.appearances = 0
                    statistics.starts = 0
                    statistics.substitute_appearances = 0
            db.add(statistics)

            local_group = next(
                (
                    ids
                    for team_players in local_names_by_team.values()
                    for ids in team_players.values()
                    if player_id in ids
                ),
                {player_id},
            )
            related_players = [
                player
                for player in db.scalars(select(Player).where(Player.id.in_(local_group))).all()
            ]
            known_nationalities = {
                player.nationality for player in related_players if player.nationality
            }
            known_birth_dates = {
                player.birth_date for player in related_players if player.birth_date
            }
            official_birth_date = _parse_profile_date(element.get("birth_date"))
            position = POSITION_BY_ELEMENT_TYPE.get(int(element.get("element_type") or 0))
            for player in related_players:
                profile_changed = False
                nationality = (
                    next(iter(known_nationalities)) if len(known_nationalities) == 1 else None
                )
                birth_date = official_birth_date or (
                    next(iter(known_birth_dates)) if len(known_birth_dates) == 1 else None
                )
                if nationality and player.nationality is None:
                    player.nationality = nationality
                    profile_changed = True
                if birth_date and player.birth_date is None:
                    player.birth_date = birth_date
                    profile_changed = True
                # FPL's position taxonomy is consistent across the squad and replaces
                # provider labels such as "Defence" or localized category plural forms.
                if position and player.position != position:
                    player.position = position
                    profile_changed = True
                if profile_changed:
                    player.profile_source_id = source_id
                    player.profile_updated_at = now

            memberships = db.scalars(
                select(PlayerTeamSeason).where(
                    PlayerTeamSeason.player_id == player_id,
                    PlayerTeamSeason.competition_season_id == competition_season_id,
                )
            ).all()
            squad_number = element.get("squad_number")
            for membership in memberships:
                if (
                    isinstance(squad_number, int)
                    and squad_number > 0
                    and membership.shirt_number is None
                ):
                    membership.shirt_number = squad_number
                if position and membership.position != position:
                    membership.position = position
            updated += 1

    if errors or unmatched:
        status = SyncStatus.PARTIAL if updated else SyncStatus.FAILED
    else:
        status = SyncStatus.SUCCESS
    label = (
        f"FPL: perfiles y estadísticas actualizados para {updated} registros de jugadores "
        f"de Premier League {season_label}; "
        f"{live_rounds_ok}/{len(completed_events)} jornadas cerradas"
    )
    if unmatched:
        label += f"; {len(unmatched)} nombres sin coincidencia única"
    if errors:
        label += f"; {errors} consultas fallidas"
    return PremierLeagueFplSyncSummary(
        status, request_count, updated, errors + len(unmatched), tuple(unmatched), label
    )
