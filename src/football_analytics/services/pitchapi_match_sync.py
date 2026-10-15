"""Import current-season team stats and scorer assists from PitchAPI."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from football_analytics.config.settings import get_settings
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchTeamStatistic,
    Player,
    PlayerCompetitionSeasonStatistic,
    PlayerMatchStatistic,
    PlayerTeamSeason,
    Season,
    SourceMatch,
    SourcePlayer,
    Team,
    TeamAlias,
)
from football_analytics.domain.enums import SyncStatus
from football_analytics.providers.pitchapi import PitchAPIProvider
from football_analytics.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]
SOURCE_CODE = "pitchapi"
SEASON_LABEL = "2026/2027"
RECENT_REFRESH_DAYS = 7
TARGETS = {
    "PL": ("ENG", "Premier League"),
    "BUNDESLIGA": ("GER", "Bundesliga"),
    "EREDIVISIE": ("NED", "Eredivisie"),
    "UCL": ("INT-2", "Champions League"),
    "UEL": ("INT-2", "Europa League"),
    "LALIGA": ("ESP", "LaLiga"),
    "LIGUE_1": ("FRA", "Ligue 1"),
    "SERIE_A": ("ITA", "Serie A"),
}

# The local catalog uses full legal club names; PitchAPI publishes short display names.
TEAM_ALIASES = {
    "PL": {
        "Arsenal": "Arsenal FC",
        "Aston Villa": "Aston Villa FC",
        "Bournemouth": "AFC Bournemouth",
        "Brentford": "Brentford FC",
        "Brighton": "Brighton & Hove Albion FC",
        "Brighton & Hove Albion": "Brighton & Hove Albion FC",
        "Burnley": "Burnley FC",
        "Chelsea": "Chelsea FC",
        "Crystal Palace": "Crystal Palace FC",
        "Everton": "Everton FC",
        "Fulham": "Fulham FC",
        "Leeds United": "Leeds United FC",
        "Liverpool": "Liverpool FC",
        "Manchester City": "Manchester City FC",
        "Manchester United": "Manchester United FC",
        "Newcastle United": "Newcastle United FC",
        "Nottingham Forest": "Nottingham Forest FC",
        "Sunderland": "Sunderland AFC",
        "Tottenham Hotspur": "Tottenham Hotspur FC",
        "West Ham United": "West Ham United FC",
        "Wolverhampton Wanderers": "Wolverhampton Wanderers FC",
    },
    "BUNDESLIGA": {
        "Augsburg": "FC Augsburg",
        "Bayer Leverkusen": "Bayer 04 Leverkusen",
        "Bayern München": "FC Bayern München",
        "Elversberg": "SV 07 Elversberg",
        "Freiburg": "SC Freiburg",
        "Hoffenheim": "TSG 1899 Hoffenheim",
        "Mainz 05": "1. FSV Mainz 05",
        "Paderborn": "SC Paderborn 07",
        "Schalke 04": "FC Schalke 04",
        "Union Berlin": "1. FC Union Berlin",
        "Werder Bremen": "SV Werder Bremen",
    },
    "EREDIVISIE": {
        "AZ Alkmaar": "AZ",
        "Ajax": "AFC Ajax",
        "Cambuur": "SC Cambuur-Leeuwarden",
        "Excelsior": "SBV Excelsior",
        "FC Twente": "FC Twente '65",
        "Feyenoord": "Feyenoord Rotterdam",
        "NEC Nijmegen": "NEC",
        "PSV Eindhoven": "PSV",
        "Telstar": "Telstar 1963",
        "Willem II": "Willem II Tilburg",
    },
    "UCL": {
        "AEK Athens": "PAE AEK",
        "Atlético Madrid": "Club Atlético de Madrid",
        "Bayern München": "FC Bayern München",
        "Bodø/Glimt": "FK Bodø/Glimt",
        "Club Brugge": "Club Brugge KV",
        "Como": "Como 1907",
        "Fenerbahçe": "Fenerbahçe SK",
        "Inter": "FC Internazionale Milano",
        "LASK": "LASK Linz",
        "Lens": "Racing Club de Lens",
        "PSV Eindhoven": "PSV",
        "Paris Saint-Germain": "Paris Saint-Germain FC",
        "Roma": "AS Roma",
        "Manchester United": "Manchester United FC",
        "Shakhtar Donetsk": "FK Shakhtar Donetsk",
        "Slavia Prague": "SK Slavia Praha",
        "Slovan Bratislava": "ŠK Slovan Bratislava",
        "Sporting CP": "Sporting Clube de Portugal",
        "Viking": "Viking FK",
    },
    "UEL": {
        "Anderlecht": "RSC Anderlecht",
        "Ararat Armenia": "FC Ararat-Armenia",
        "Bayer Leverkusen": "Bayer 04 Leverkusen",
        "Benfica": "Benfica Lissabon",
        "Beşiktaş": "Besiktas Istanbul",
        "Celtic": "Celtic Glasgow",
        "Ferencváros": "Ferencváros Budapest",
        "Hapoel Beer Sheva": "Hapoel Beer Scheva",
        "Hoffenheim": "TSG Hoffenheim",
        "Jagiellonia Białystok": "Jagiellonia Bialystok",
        "Juventus": "Juventus Turin",
        "Lillestrøm": "Lillestrøm SK",
        "Milan": "AC Mailand",
        "Lyon": "Olympique Lyon",
        "Marseille": "Olympique Marseille",
        "OFI Crete": "OFI Kreta",
        "Olympiacos": "Olympiakos Piräus",
        "Omonia Nicosia": "Omonia Nikosia",
        "Salzburg": "RB Salzburg",
        "Sparta Prague": "Sparta Prag",
        "Rennes": "Stade Rennes",
        "Torreense": "SC União Torreense",
        "Union St.Gilloise": "Union Saint-Gilloise",
        "Viktoria Plzeň": "Viktoria Pilsen",
    },
    "LALIGA": {
        "Atlético Madrid": "Club Atlético de Madrid",
        "Barcelona": "FC Barcelona",
        "Celta Vigo": "RC Celta de Vigo",
        "Deportivo A Coruña": "RC Deportivo La Coruña",
        "Elche": "Elche CF",
        "Espanyol": "RCD Espanyol de Barcelona",
        "Getafe": "Getafe CF",
        "Levante": "Levante UD",
        "Málaga": "Málaga CF",
        "Osasuna": "CA Osasuna",
        "Racing Santander": "Real Racing Club de Santander",
        "Rayo Vallecano": "Rayo Vallecano de Madrid",
        "Real Betis": "Real Betis Balompié",
        "Real Madrid": "Real Madrid CF",
        "Real Sociedad": "Real Sociedad de Fútbol",
        "Sevilla": "Sevilla FC",
        "Valencia": "Valencia CF",
        "Villarreal": "Villarreal CF",
    },
    "LIGUE_1": {
        "Angers": "Angers SCO",
        "Auxerre": "AJ Auxerre",
        "Brest": "Stade Brestois 29",
        "Le Havre": "Le Havre AC",
        "Le Mans": "Le Mans FC",
        "Lens": "Racing Club de Lens",
        "Lille": "Lille OSC",
        "Lorient": "FC Lorient",
        "Lyon": "Olympique Lyonnais",
        "Marseille": "Olympique de Marseille",
        "Monaco": "AS Monaco FC",
        "Nice": "OGC Nice",
        "Paris Saint-Germain": "Paris Saint-Germain FC",
        "Rennes": "Stade Rennais FC 1901",
        "Strasbourg": "RC Strasbourg Alsace",
        "Troyes": "ES Troyes AC",
    },
    "SERIE_A": {},
}


@dataclass(frozen=True, slots=True)
class PitchAPISyncSummary:
    run_id: UUID | None
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


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


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float) and value.is_integer():
        return max(int(value), 0)
    if isinstance(value, str):
        match = re.match(r"\s*(\d+)", value)
        return int(match.group(1)) if match else None
    return None


def _team_stat_values(full_stats: object) -> dict[str, dict[str, int | None]]:
    """Extract the published full-match team totals from PitchAPI's grouped payload."""
    if not isinstance(full_stats, dict):
        return {}
    items = {
        str(item.get("key", "")).casefold(): item
        for group in full_stats.get("groups", [])
        if isinstance(group, dict)
        for item in group.get("items", [])
        if isinstance(item, dict)
    }
    fields = {
        "possession": ("ballpossesion", "ball_possession"),
        "shots_total": ("total_shots", "shots"),
        "shots_on_target": ("shotsontarget", "shots_on_target"),
        "shots_off_target": ("shotsofftarget", "shots_off_target"),
        "shots_blocked": ("blocked_shots",),
        "shots_inside_box": ("shots_inside_box",),
        "shots_outside_box": ("shots_outside_box",),
        "corners": ("corners",),
        "fouls": ("fouls",),
        "offsides": ("offsides",),
        "yellow_cards": ("yellow_cards",),
        "red_cards": ("red_cards",),
        "passes_total": ("passes",),
        "passes_accurate": ("accurate_passes",),
        "goalkeeper_saves": ("keeper_saves",),
    }
    values: dict[str, dict[str, int | None]] = {}
    for side in ("home", "away"):
        values[side] = {}
        for field, keys in fields.items():
            item = next((items[key] for key in keys if key in items), None)
            values[side][field] = _number(item.get(side)) if item else None
    return values


def _stat_values(groups: object) -> dict[str, int]:
    values: dict[str, int] = {}
    if not isinstance(groups, list):
        return values
    for group in groups:
        stats = group.get("stats") if isinstance(group, dict) else None
        if not isinstance(stats, dict):
            continue
        for item in stats.values():
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
            number = _number(stat.get("value"))
            if isinstance(key, str) and number is not None:
                values[key.casefold()] = number
    return values


def _team_key(value: str) -> str:
    return normalize_name(value)


def _source(session: Session) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
    if source is None:
        source = DataSource(
            code=SOURCE_CODE,
            name="PitchAPI",
            base_url="https://api.pitchapi.dev/v1",
            priority=8,
            enabled=True,
            requires_api_key=True,
            license_name="Free API plan; data reuse terms not specified in API documentation",
            attribution_text="Datos y estadísticas de fútbol proporcionados por PitchAPI",
            terms_url="https://pitchapi.dev/",
        )
        session.add(source)
        session.flush()
    return source


def _match_teams(session: Session, competition_season_id: int) -> dict[str, set[int]]:
    matches = session.scalars(
        select(Match).where(Match.competition_season_id == competition_season_id)
    ).all()
    team_ids = {
        team_id for match in matches for team_id in (match.home_team_id, match.away_team_id)
    }
    result: dict[str, set[int]] = defaultdict(set)
    for team in session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
        for value in (team.canonical_name, team.short_name):
            if value:
                result[_team_key(value)].add(team.id)
    for alias, team_id in session.execute(
        select(TeamAlias.alias, TeamAlias.team_id).where(TeamAlias.team_id.in_(team_ids))
    ):
        result[_team_key(alias)].add(team_id)
    return result


def _player_targets(
    session: Session, competition_season_id: int
) -> dict[int, list[tuple[int, str, int | None]]]:
    """Return every active player attached to this season, not only scorers.

    Squad feeds often have no season-stat row yet for players with zero goals.
    The active roster association is therefore the source of truth for who to
    try to resolve; any available season appearance count is only a validator.
    """
    targets: dict[int, list[tuple[int, str, int | None]]] = defaultdict(list)
    pitchapi = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
    statistic_source = aliased(DataSource)
    rows = session.execute(
        select(
            Player.id,
            Player.display_name,
            Player.birth_date,
            PlayerTeamSeason.team_id,
            PlayerCompetitionSeasonStatistic.appearances,
            DataSource.code,
            DataSource.priority,
            SourcePlayer.player_id.is_not(None),
            statistic_source.code,
        )
        .join(
            PlayerTeamSeason,
            PlayerTeamSeason.player_id == Player.id,
        )
        .join(DataSource, DataSource.id == PlayerTeamSeason.source_id)
        .outerjoin(
            PlayerCompetitionSeasonStatistic,
            (PlayerCompetitionSeasonStatistic.player_id == Player.id)
            & (PlayerCompetitionSeasonStatistic.competition_season_id == competition_season_id),
        )
        .outerjoin(
            statistic_source,
            statistic_source.id == PlayerCompetitionSeasonStatistic.source_id,
        )
        .outerjoin(
            SourcePlayer,
            (SourcePlayer.player_id == Player.id)
            & (SourcePlayer.source_id == (pitchapi.id if pitchapi else -1)),
        )
        .where(
            PlayerTeamSeason.competition_season_id == competition_season_id,
            PlayerTeamSeason.active.is_(True),
        )
        .order_by(Player.id)
    ).all()
    candidates: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for (
        player_id,
        name,
        birth_date,
        team_id,
        appearances,
        source_code,
        priority,
        known_identity,
        statistic_source_code,
    ) in rows:
        candidate = candidates[team_id].setdefault(
            player_id,
            {
                "name": name,
                "birth_date": birth_date,
                "appearances": None,
                "sources": set(),
                "priority": priority,
                "known_identity": False,
            },
        )
        candidate["sources"].add(source_code)
        candidate["priority"] = min(candidate["priority"], priority)
        candidate["known_identity"] = candidate["known_identity"] or known_identity
        if candidate["birth_date"] is None and birth_date is not None:
            candidate["birth_date"] = birth_date
        if appearances is not None and statistic_source_code != "football-data":
            candidate["appearances"] = max(candidate["appearances"] or 0, appearances)
    for team_id, player_candidates in candidates.items():
        duplicate_groups: list[list[tuple[int, dict[str, Any]]]] = []
        for item in player_candidates.items():
            player_id, candidate = item
            group = next(
                (
                    existing_group
                    for existing_group in duplicate_groups
                    if any(
                        _same_player_entity(
                            candidate["name"],
                            candidate["birth_date"],
                            existing["name"],
                            existing["birth_date"],
                            trusted_identity_conflict=(
                                candidate["known_identity"] or existing["known_identity"]
                            ),
                        )
                        for _existing_id, existing in existing_group
                    )
                ),
                None,
            )
            if group is None:
                duplicate_groups.append([item])
            else:
                group.append(item)
        for duplicate_group in duplicate_groups:
            player_id, candidate = min(
                duplicate_group,
                key=lambda item: (
                    not item[1]["known_identity"],
                    "football-data" not in item[1]["sources"],
                    item[1]["appearances"] is None,
                    item[1]["priority"],
                    item[0],
                ),
            )
            names = [value["name"] for _id, value in duplicate_group]
            display_name = max(names, key=lambda value: (len(value.split()), len(value)))
            expected_appearances = max(
                (
                    value["appearances"]
                    for _id, value in duplicate_group
                    if value["appearances"] is not None
                ),
                default=None,
            )
            targets[team_id].append((player_id, display_name, expected_appearances))
    return targets


def _same_player_entity(
    first_name: str,
    first_birth_date: date | None,
    second_name: str,
    second_birth_date: date | None,
    *,
    trusted_identity_conflict: bool = False,
) -> bool:
    first_normalized = normalize_name(first_name)
    second_normalized = normalize_name(second_name)
    if trusted_identity_conflict and _same_verified_player_name(first_name, second_name):
        return True
    if first_normalized == second_normalized:
        return (
            not (first_birth_date and second_birth_date)
            or first_birth_date == second_birth_date
            or trusted_identity_conflict
        )
    return (
        first_birth_date is not None
        and first_birth_date == second_birth_date
        and _same_player_name_variant(first_name, second_name)
    )


def _same_verified_player_name(first_name: str, second_name: str) -> bool:
    first = tuple(normalize_name(first_name).replace("-", " ").split())
    second = tuple(normalize_name(second_name).replace("-", " ").split())
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    if len(shorter) >= 2:
        index = 0
        for token in longer:
            if token == shorter[index]:
                index += 1
                if index == len(shorter):
                    return True
    return (
        len(first) >= 2
        and len(first) == len(second)
        and first[1:] == second[1:]
        and SequenceMatcher(None, first[0], second[0]).ratio() >= 0.75
    )


def _player_values(row: dict[str, Any]) -> dict[str, int | None]:
    stats = _stat_values(row.get("stats"))
    return {
        "minutes_played": stats.get("minutes_played"),
        "goals": stats.get("goals"),
        "assists": stats.get("assists"),
        "yellow_cards": stats.get("yellow_cards"),
        "red_cards": stats.get("red_cards"),
        "shots_total": stats.get("total_shots"),
        # PitchAPI exposes the on-target count as the numerator of its
        # shot_accuracy fraction (value / total) in player match stats.
        "shots_on_target": stats.get(
            "shots_on_target",
            stats.get("shotsontarget", stats.get("shot_accuracy")),
        ),
        "goalkeeper_saves": stats.get("keeper_saves"),
        "goals_conceded": stats.get("goals_conceded"),
    }


def sync_pitchapi_match_statistics(
    session_factory: SessionFactory,
    *,
    provider: PitchAPIProvider | None = None,
    competition_codes: tuple[str, ...] | None = None,
    force_player_refresh: bool = False,
) -> PitchAPISyncSummary:
    """Import published team match stats and verify scorer assist totals from match lines."""
    provider = provider or PitchAPIProvider()
    if not get_settings().pitchapi_api_key.get_secret_value() and provider.api_key == "":
        return PitchAPISyncSummary(None, SyncStatus.FAILED, 0, 0, 0, 0, 1, "Falta PITCHAPI_API_KEY")

    inserted = updated = skipped = errors = 0
    notes: list[str] = []
    codes = set(competition_codes) if competition_codes else set(TARGETS)
    codes &= set(TARGETS)
    with session_factory() as session:
        source = _source(session)
        source_id = source.id
        competitions = session.execute(
            select(Competition, CompetitionSeason, Season)
            .join(CompetitionSeason, CompetitionSeason.competition_id == Competition.id)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .where(
                Competition.code.in_(codes),
                Season.start_year == 2026,
                Season.end_year == 2027,
            )
        ).all()
        league_catalog = provider.get_leagues()
        for competition, competition_season, _season in competitions:
            country_code, league_name = TARGETS[competition.code]
            league = next(
                (
                    row
                    for row in league_catalog
                    if row.get("country_code") == country_code
                    and row.get("name") == league_name
                    and SEASON_LABEL in (row.get("seasons") or [])
                ),
                None,
            )
            if league is None:
                errors += 1
                notes.append(f"{competition.code}: PitchAPI no publica 2026/27")
                continue
            try:
                matches = provider.get_league_matches(str(league["id"]), SEASON_LABEL)
                team_ids_by_name = _match_teams(session, competition_season.id)
                competition_team_ids = {
                    team_id for ids in team_ids_by_name.values() for team_id in ids
                }
                canonical_ids = {
                    _team_key(team.canonical_name): team.id
                    for team in session.scalars(select(Team)).all()
                    if team.id in competition_team_ids
                }
                alias_map = TEAM_ALIASES.get(competition.code, {})
                local_matches = session.scalars(
                    select(Match).where(
                        Match.competition_season_id == competition_season.id,
                        Match.status == "FINISHED",
                        Match.home_score_ft.is_not(None),
                        Match.away_score_ft.is_not(None),
                    )
                ).all()
                targets = _player_targets(session, competition_season.id)
                for api_match in matches:
                    try:
                        status = str(api_match.get("status", "")).casefold()
                        if status and status not in {"finished", "played"}:
                            skipped += 1
                            continue
                        home = api_match.get("home_team") or {}
                        away = api_match.get("away_team") or {}
                        home_name = str(home.get("name") or "")
                        away_name = str(away.get("name") or "")
                        home_canonical = alias_map.get(home_name, home_name)
                        away_canonical = alias_map.get(away_name, away_name)
                        home_ids = team_ids_by_name.get(_team_key(home_canonical), set())
                        away_ids = team_ids_by_name.get(_team_key(away_canonical), set())
                        if not home_ids:
                            home_id = canonical_ids.get(_team_key(home_canonical))
                            home_ids = {home_id} if home_id else set()
                        if not away_ids:
                            away_id = canonical_ids.get(_team_key(away_canonical))
                            away_ids = {away_id} if away_id else set()
                        if len(home_ids) != 1 or len(away_ids) != 1:
                            skipped += 1
                            continue
                        home_id, away_id = next(iter(home_ids)), next(iter(away_ids))
                        day = date.fromisoformat(str(api_match["date"])[:10])
                        candidates = [
                            match
                            for match in local_matches
                            if match.home_team_id == home_id
                            and match.away_team_id == away_id
                            and abs((match.kickoff_utc.date() - day).days) <= 1
                        ]
                        if len(candidates) != 1:
                            skipped += 1
                            continue
                        local_match = candidates[0]
                        api_home_score = _number(api_match.get("score_home"))
                        api_away_score = _number(api_match.get("score_away"))
                        if (
                            api_home_score is not None
                            and api_away_score is not None
                            and (api_home_score, api_away_score)
                            != (local_match.home_score_ft, local_match.away_score_ft)
                        ):
                            skipped += 1
                            notes.append(
                                f"{competition.code} {day}: marcador distinto; partido omitido"
                            )
                            continue

                        source_match = session.scalar(
                            select(SourceMatch).where(
                                SourceMatch.source_id == source_id,
                                SourceMatch.external_id == str(api_match["id"]),
                            )
                        )
                        if source_match is None:
                            source_match = SourceMatch(
                                source_id=source_id,
                                external_id=str(api_match["id"]),
                                match_id=local_match.id,
                            )
                            session.add(source_match)
                            session.flush()
                        elif source_match.match_id != local_match.id:
                            skipped += 1
                            continue

                        current_rows = session.scalars(
                            select(MatchTeamStatistic).where(
                                MatchTeamStatistic.match_id == local_match.id,
                                MatchTeamStatistic.source_id == source_id,
                                MatchTeamStatistic.team_id.in_((home_id, away_id)),
                            )
                        ).all()
                        current_by_team = {row.team_id: row for row in current_rows}
                        cutoff = datetime.now(UTC).date() - timedelta(days=RECENT_REFRESH_DAYS)
                        provider_complete = all(
                            team_id in current_by_team
                            and all(
                                getattr(current_by_team[team_id], field) is not None
                                for field in (
                                    "corners",
                                    "yellow_cards",
                                    "red_cards",
                                    "shots_total",
                                    "shots_on_target",
                                    "possession",
                                )
                            )
                            for team_id in (home_id, away_id)
                        )
                        needs_refresh = (
                            force_player_refresh
                            or source_match.raw_hash is None
                            or local_match.kickoff_utc.date() >= cutoff
                            or not provider_complete
                        )
                        if not needs_refresh:
                            skipped += 1
                            continue

                        team_payload = provider.get_match_stats(str(api_match["id"]))
                        player_payload = provider.get_match_players(str(api_match["id"]))
                        lineup_payload = provider.get_match_lineups(str(api_match["id"]))
                        shot_payload = provider.get_match_shots(str(api_match["id"]))
                        event_payload = provider.get_match_events(str(api_match["id"]))
                        lineup_status: dict[str, tuple[bool, int | None]] = {}
                        for side in ("home", "away"):
                            lineup = lineup_payload.get(side) or {}
                            for is_substitute, key in ((False, "starters"), (True, "subs")):
                                for lineup_player in lineup.get(key, []) or []:
                                    if not isinstance(lineup_player, dict):
                                        continue
                                    lineup_player_id = str(lineup_player.get("player_id") or "")
                                    if lineup_player_id:
                                        shirt_number = _number(lineup_player.get("shirt_number"))
                                        lineup_status[lineup_player_id] = (
                                            is_substitute,
                                            shirt_number,
                                        )
                        shot_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
                        for shot in shot_payload:
                            shot_player = shot.get("player") or {}
                            shot_player_id = str(
                                shot_player.get("id") or shot.get("player_id") or ""
                            )
                            if not shot_player_id:
                                continue
                            shot_totals[shot_player_id][0] += 1
                            if shot.get("is_on_target") is True:
                                shot_totals[shot_player_id][1] += 1
                        card_totals: dict[str, dict[str, int]] = defaultdict(
                            lambda: {"yellow_cards": 0, "red_cards": 0}
                        )
                        for event in event_payload:
                            event_player = event.get("player") or {}
                            event_player_id = str(
                                event_player.get("id") or event.get("player_id") or ""
                            )
                            event_type = str(event.get("event_type") or "").casefold()
                            if not event_player_id:
                                continue
                            if event_type in {
                                "yellowcard",
                                "yellow_card",
                                "yellow card",
                                "secondyellow",
                                "second_yellow",
                                "second yellow",
                            }:
                                card_totals[event_player_id]["yellow_cards"] += 1
                            if event_type in {
                                "redcard",
                                "red_card",
                                "red card",
                                "secondyellow",
                                "second_yellow",
                                "second yellow",
                            }:
                                card_totals[event_player_id]["red_cards"] += 1
                        full_stats = next(
                            (
                                period
                                for period in team_payload.get("periods", [])
                                if isinstance(period, dict) and period.get("period") == "All"
                            ),
                            None,
                        )
                        parsed_team_values = _team_stat_values(full_stats)
                        team_values = {
                            team_id: parsed_team_values.get(side, {})
                            for team_id, side in ((home_id, "home"), (away_id, "away"))
                        }
                        for team_id in (home_id, away_id):
                            values = team_values.get(team_id, {})
                            if not any(value is not None for value in values.values()):
                                continue
                            row = current_by_team.get(team_id)
                            if row is None:
                                row = MatchTeamStatistic(
                                    match_id=local_match.id,
                                    team_id=team_id,
                                    source_id=source_id,
                                )
                                session.add(row)
                                current_by_team[team_id] = row
                                inserted += 1
                            else:
                                updated += 1
                            for field, value in values.items():
                                if value is not None:
                                    setattr(row, field, value)
                            row.source_updated_at = datetime.now(UTC)

                        team_id_by_api_id = {
                            str(home.get("id")): home_id,
                            str(away.get("id")): away_id,
                        }
                        unresolved = 0
                        for player_row in player_payload:
                            profile = player_row.get("player") or {}
                            external_player_id = str(profile.get("id") or "")
                            player_name = str(profile.get("name") or "").strip()
                            team_id = team_id_by_api_id.get(str(player_row.get("team_id")))
                            if not external_player_id or not player_name or team_id not in targets:
                                continue
                            values = _player_values(player_row)
                            if not values.get("minutes_played"):
                                continue
                            values["shots_total"], values["shots_on_target"] = shot_totals.get(
                                external_player_id, [0, 0]
                            )
                            values["yellow_cards"] = card_totals[external_player_id]["yellow_cards"]
                            values["red_cards"] = card_totals[external_player_id]["red_cards"]
                            possible: list[str] = []
                            candidates = {
                                player_id
                                for player_id, target_name, _appearances in targets[team_id]
                                if _same_player_name_variant(player_name, target_name)
                            }
                            if len(candidates) != 1:
                                surname = normalize_name(player_name).split()[-1:]
                                possible = [
                                    target_name
                                    for _player_id, target_name, _appearances in targets[team_id]
                                    if surname
                                    and normalize_name(target_name).split()[-1:] == surname
                                ]
                                if possible:
                                    try:
                                        complete_name = provider.get_player(external_player_id).get(
                                            "name"
                                        )
                                    except Exception:
                                        complete_name = None
                                    if isinstance(complete_name, str):
                                        candidates = {
                                            player_id
                                            for player_id, target_name, _appearances in targets[
                                                team_id
                                            ]
                                            if _same_player_name_variant(complete_name, target_name)
                                        }
                                elif len(normalize_name(player_name).split()) == 1:
                                    first_name = normalize_name(player_name)
                                    first_name_matches = {
                                        player_id
                                        for player_id, target_name, _appearances in targets[team_id]
                                        if normalize_name(target_name).split()[:1] == [first_name]
                                    }
                                    if len(first_name_matches) == 1:
                                        candidates = first_name_matches
                            if len(candidates) != 1:
                                # Normal squad members are expected in the endpoint. Count only
                                # unmatched scoring contributions or plausible scorer identities.
                                if (
                                    (values.get("goals") or 0) > 0
                                    or (values.get("assists") or 0) > 0
                                    or possible
                                ):
                                    unresolved += 1
                                continue
                            player_id = next(iter(candidates))
                            starter_info = lineup_status.get(external_player_id)
                            existing_identity = session.scalar(
                                select(SourcePlayer).where(
                                    SourcePlayer.source_id == source_id,
                                    SourcePlayer.external_id == external_player_id,
                                )
                            )
                            if existing_identity is None:
                                session.add(
                                    SourcePlayer(
                                        source_id=source_id,
                                        external_id=external_player_id,
                                        player_id=player_id,
                                    )
                                )
                            elif existing_identity.player_id != player_id:
                                unresolved += 1
                                continue
                            statistic = session.scalar(
                                select(PlayerMatchStatistic).where(
                                    PlayerMatchStatistic.match_id == local_match.id,
                                    PlayerMatchStatistic.player_id == player_id,
                                    PlayerMatchStatistic.source_id == source_id,
                                )
                            )
                            values = {
                                key: value for key, value in values.items() if value is not None
                            }
                            if statistic is None:
                                statistic = PlayerMatchStatistic(
                                    match_id=local_match.id,
                                    player_id=player_id,
                                    team_id=team_id,
                                    source_id=source_id,
                                )
                                session.add(statistic)
                                inserted += 1
                            else:
                                updated += 1
                            for field, value in values.items():
                                if field in {
                                    "minutes_played",
                                    "goals",
                                    "assists",
                                    "yellow_cards",
                                    "red_cards",
                                    "shots_total",
                                    "shots_on_target",
                                    "goalkeeper_saves",
                                    "goals_conceded",
                                }:
                                    setattr(statistic, field, value)
                            conceded = values.get("goals_conceded")
                            if conceded is not None:
                                statistic.clean_sheet = conceded == 0
                            if starter_info is not None:
                                statistic.is_substitute = starter_info[0]
                                if starter_info[1] is not None:
                                    statistic.shirt_number = starter_info[1]
                            statistic.team_id = team_id
                            statistic.source_updated_at = datetime.now(UTC)
                        skipped += unresolved
                        digest = hashlib.sha256(
                            json.dumps(
                                {
                                    "team": team_payload,
                                    "players": player_payload,
                                    "lineups": lineup_payload,
                                    "shots": shot_payload,
                                    "events": event_payload,
                                },
                                sort_keys=True,
                                ensure_ascii=False,
                            ).encode("utf-8")
                        ).hexdigest()
                        source_match.raw_hash = digest
                        source_match.source_updated_at = datetime.now(UTC)
                        session.commit()
                    except Exception as exc:
                        session.rollback()
                        errors += 1
                        notes.append(
                            f"{competition.code} {api_match.get('date', '')}: "
                            f"{exc.__class__.__name__}"
                        )

                targets = _player_targets(session, competition_season.id)
                completed_assists = incomplete_assists = 0
                for team_id, team_targets in targets.items():
                    team_matches = [
                        match
                        for match in local_matches
                        if team_id in (match.home_team_id, match.away_team_id)
                    ]
                    covered_match_ids = (
                        set(
                            session.scalars(
                                select(SourceMatch.match_id).where(
                                    SourceMatch.source_id == source_id,
                                    SourceMatch.match_id.in_([match.id for match in team_matches]),
                                    SourceMatch.raw_hash.is_not(None),
                                )
                            ).all()
                        )
                        if team_matches
                        else set()
                    )
                    team_coverage_complete = bool(team_matches) and len(covered_match_ids) == len(
                        {match.id for match in team_matches}
                    )
                    for player_id, _name, expected_appearances in team_targets:
                        rows = session.scalars(
                            select(PlayerMatchStatistic).where(
                                PlayerMatchStatistic.source_id == source_id,
                                PlayerMatchStatistic.player_id == player_id,
                                PlayerMatchStatistic.team_id == team_id,
                                PlayerMatchStatistic.match_id.in_(
                                    select(Match.id).where(
                                        Match.competition_season_id == competition_season.id
                                    )
                                ),
                                PlayerMatchStatistic.minutes_played > 0,
                            )
                        ).all()
                        values = [row.assists for row in rows]
                        player_coverage_complete = team_coverage_complete and (
                            (expected_appearances is not None and len(rows) == expected_appearances)
                            or (expected_appearances is None and bool(rows))
                        )
                        statistic = session.scalar(
                            select(PlayerCompetitionSeasonStatistic).where(
                                PlayerCompetitionSeasonStatistic.player_id == player_id,
                                PlayerCompetitionSeasonStatistic.competition_season_id
                                == competition_season.id,
                                PlayerCompetitionSeasonStatistic.source_id == source_id,
                            )
                        )
                        if statistic is None:
                            statistic = PlayerCompetitionSeasonStatistic(
                                player_id=player_id,
                                competition_season_id=competition_season.id,
                                source_id=source_id,
                            )
                            session.add(statistic)
                        statistic.assists = (
                            sum(values)
                            if player_coverage_complete
                            and all(value is not None for value in values)
                            else None
                        )
                        statistic.appearances = len(rows) if player_coverage_complete else None
                        statistic.starts = (
                            sum(row.is_substitute is False for row in rows)
                            if player_coverage_complete
                            and all(row.is_substitute is not None for row in rows)
                            else None
                        )
                        statistic.substitute_appearances = (
                            sum(row.is_substitute is True for row in rows)
                            if player_coverage_complete
                            and all(row.is_substitute is not None for row in rows)
                            else None
                        )
                        statistic.minutes_played = (
                            sum(
                                row.minutes_played for row in rows if row.minutes_played is not None
                            )
                            if player_coverage_complete
                            and all(row.minutes_played is not None for row in rows)
                            else None
                        )
                        for field in (
                            "goals",
                            "yellow_cards",
                            "red_cards",
                            "shots_total",
                            "shots_on_target",
                        ):
                            field_values = [getattr(row, field) for row in rows]
                            setattr(
                                statistic,
                                field,
                                sum(field_values)
                                if player_coverage_complete
                                and all(value is not None for value in field_values)
                                else None,
                            )
                        keeper_rows = [row for row in rows if row.goals_conceded is not None]
                        statistic.goals_conceded = (
                            sum(row.goals_conceded for row in keeper_rows)
                            if player_coverage_complete
                            and keeper_rows
                            and all(row.goals_conceded is not None for row in keeper_rows)
                            else None
                        )
                        statistic.clean_sheets = (
                            sum(row.clean_sheet is True for row in keeper_rows)
                            if player_coverage_complete
                            and keeper_rows
                            and all(row.clean_sheet is not None for row in keeper_rows)
                            else None
                        )
                        if player_coverage_complete and not rows and expected_appearances == 0:
                            player = session.get(Player, player_id)
                            position = normalize_name(player.position or "") if player else ""
                            if position in {
                                "g",
                                "gk",
                                "goalkeeper",
                                "keeper",
                                "portero",
                                "arquero",
                            }:
                                statistic.goals_conceded = 0
                                statistic.clean_sheets = 0
                        statistic.source_updated_at = datetime.now(UTC)
                        if statistic.assists is not None:
                            completed_assists += 1
                        else:
                            incomplete_assists += 1
                session.commit()
                notes.append(
                    f"{competition.code}: {len(matches)} partidos API, "
                    f"{completed_assists} asistencias verificadas, "
                    f"{incomplete_assists} jugadores con cobertura incompleta"
                )
            except Exception as exc:
                session.rollback()
                errors += 1
                notes.append(f"{competition.code}: {exc.__class__.__name__}")

    status = (
        SyncStatus.FAILED
        if errors and not inserted and not updated
        else (SyncStatus.PARTIAL if errors else SyncStatus.SUCCESS)
    )
    message = (
        f"PitchAPI: {inserted} filas nuevas, {updated} actualizadas, "
        f"{skipped} sin asociación segura, {errors} errores. " + "; ".join(notes[:8])
    )
    return PitchAPISyncSummary(
        None, status, provider.request_count, inserted, updated, skipped, errors, message
    )
