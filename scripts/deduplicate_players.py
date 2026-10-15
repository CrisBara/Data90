"""Safely consolidate duplicate player identities from the active season.

Dry-run by default. The explicit ``--apply`` option creates a compressed,
row-level backup before changing the local database.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from football_analytics.config.settings import get_settings
from football_analytics.database.models import (
    CompetitionSeason,
    DataSource,
    Match,
    MatchEvent,
    Player,
    PlayerCompetitionSeasonStatistic,
    PlayerMatchStatistic,
    PlayerTeamSeason,
    Season,
    SourcePlayer,
    Team,
)
from football_analytics.database.session import make_session_factory
from football_analytics.sync.normalizer import normalize_name


# These DOBs were checked against the corresponding official league, club, or
# federation profiles; they resolve otherwise conflicting provider records.
VERIFIED_DOBS = {
    4793: date(2003, 10, 30),  # António Silva — Premier League
    8547: date(2002, 10, 21),  # Thierno Barry — Premier League
    8739: date(2005, 1, 6),  # Jacobo Ramón — LaLiga / RFEF
    8990: date(2008, 1, 17),  # Ruud Nijstad — Eredivisie
    8583: date(2004, 9, 8),  # Lewis Hall — Premier League
    8841: date(1999, 9, 30),  # Shūto Machino — Bundesliga
    8594: date(2004, 9, 3),  # Nobel Mendy — Real Betis
    8847: date(2001, 12, 9),  # Cameron Archer — Premier League
    8819: date(2004, 2, 23),  # Leon Avdullahu — TSG Hoffenheim
    8852: date(1992, 12, 23),  # Mbwana Samatta — CAF
    8987: date(2002, 12, 21),  # Ringo Meerveld — Eredivisie
    5380: date(2004, 9, 8),  # Nico Paz — football-data profile
    8846: date(2005, 3, 9),  # Adil Bourabaa — Le Mans roster / L'Équipe profile
    6949: date(2004, 9, 22),  # Abdoulaye Faye — Bayer 04 / LaLiga profile
    5039: date(2006, 1, 20),  # Kees Smit — official AZ profile
    7144: date(2002, 9, 2),  # Jon Gorrotxategi — official Real Sociedad profile
    6998: date(2002, 6, 23),  # Andrew Omobamidele — UEFA profile / Anderlecht transfer
    3669: date(2005, 3, 31),  # Malick Fofana — UEFA profile / OL transfer notice
}

NICO_IDS = {3288, 5380, 9255}
NICO_CANONICAL_ID = 5380
KAIKI_IDS = {3280, 5369, 9258}
KAIKI_CANONICAL_ID = 5369
KAIKI_CANONICAL_NAME = "Kaiki Bruno da Silva"
KAIKI_ACCEPTED_NAMES = {"kaiki", "kaiki bruno", "kaiki bruno da silva"}
KAIKI_VERIFIED_DOB = date(2003, 3, 8)
KEHRER_IDS = {3995, 8416}
KEHRER_CANONICAL_ID = 3995
KEHRER_ACCEPTED_NAMES = {"thilo kehrer", "jan thilo kehrer"}
ISAK_JENSEN_IDS = {4016, 5725}
ISAK_JENSEN_CANONICAL_ID = 5725
OSO_IDS = {3104, 7021, 8363}
OSO_CANONICAL_ID = 7021
MIKKEL_BRO_HANSEN_IDS = {4337, 5929}
MIKKEL_BRO_HANSEN_CANONICAL_ID = 4337
SERGIO_MARTINEZ_IDS = {8671, 9510}
SERGIO_MARTINEZ_CANONICAL_ID = 9510
OMOBAMIDELE_IDS = {6998, 10786}
OMOBAMIDELE_CANONICAL_ID = 6998
FOFANA_IDS = {3669, 10805}
FOFANA_CANONICAL_ID = 3669
# Verified across the exact birth date, separate provider identities, and a
# current player-match row for the club/season listed by ESPN.
VERIFIED_CROSS_PROVIDER_DUPLICATES = (
    (10316, 8371, "Tyrese Noubissie", date(2009, 5, 13)),
    (9383, 8510, "Alhaji Bah", date(2008, 2, 9)),
    (9381, 8515, "Viggo Plantinga", date(2007, 5, 18)),
)
PROFILE_FIELDS = (
    "nationality",
    "birth_date",
    "birth_place",
    "height",
    "weight",
    "position",
    "preferred_foot",
    "photo_url",
    "profile_source_id",
)
UNIQUE_CHILDREN = (
    (SourcePlayer, ("source_id", "external_id")),
    (PlayerTeamSeason, ("team_id", "competition_season_id", "source_id")),
    (PlayerCompetitionSeasonStatistic, ("competition_season_id", "source_id")),
    (PlayerMatchStatistic, ("match_id", "source_id")),
)


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _rows(session: Any, model: Any, ids: set[int]) -> list[Any]:
    if not ids:
        return []
    return session.scalars(select(model).where(model.player_id.in_(ids))).all()


def _position_group(value: str | None) -> str | None:
    normalized = normalize_name(value or "")
    if not normalized:
        return None
    if any(word in normalized for word in ("goalkeeper", "keeper", "portero", "arquero")):
        return "goalkeeper"
    if any(word in normalized for word in ("defen", "defens", "defence", "back")):
        return "defender"
    if any(word in normalized for word in ("mid", "medio", "mediocamp")):
        return "midfielder"
    if any(word in normalized for word in ("forward", "offence", "delanter", "atac")):
        return "forward"
    return normalized


def _compatible_values(left: str | None, right: str | None, *, positions: bool = False) -> bool:
    if not left or not right:
        return True
    if positions:
        return _position_group(left) == _position_group(right)
    return normalize_name(left) == normalize_name(right)


def build_plan(session: Any) -> tuple[list[tuple[int, set[int], str]], list[list[int]]]:
    active_seasons = set(session.scalars(select(Season.id).where(Season.active.is_(True))))
    memberships = session.execute(
        select(Player, PlayerTeamSeason, Team)
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .where(
            PlayerTeamSeason.active.is_(True),
            PlayerTeamSeason.competition_season_id.in_(
                select(CompetitionSeason.id).where(CompetitionSeason.season_id.in_(active_seasons))
            ),
        )
    ).all()

    groups: dict[tuple[int, str], set[int]] = defaultdict(set)
    same_birth_groups: dict[tuple[int, date], dict[int, Player]] = defaultdict(dict)
    player_current_clubs: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for player, membership, team in memberships:
        groups[(team.id, normalize_name(player.display_name))].add(player.id)
        if player.birth_date is not None:
            same_birth_groups[(team.id, player.birth_date)][player.id] = player
        player_current_clubs[player.id].add((team.id, membership.competition_season_id))

    graph: dict[int, set[int]] = defaultdict(set)
    for ids in groups.values():
        if len(ids) > 1:
            for player_id in ids:
                graph[player_id].update(ids - {player_id})

    # Global identity match: identical names and DOB are not sufficient by
    # themselves. Short football nicknames such as "Vitinha" can identify
    # different people, and a provider can attach the wrong DOB. Require the
    # records to overlap at the same active club/season and have compatible
    # positions before linking them automatically.
    all_players = session.scalars(select(Player)).all()
    player_by_id = {player.id: player for player in all_players}
    global_name_birth_groups: dict[tuple[str, date], set[int]] = defaultdict(set)
    for player in all_players:
        if player.birth_date is not None:
            global_name_birth_groups[(normalize_name(player.display_name), player.birth_date)].add(player.id)
    ambiguous_global_groups: list[list[int]] = []
    for ids in global_name_birth_groups.values():
        if len(ids) > 1:
            candidate_ids = set(ids)
            ordered_ids = sorted(candidate_ids)
            compatible_group = all(
                player_current_clubs[left_id] & player_current_clubs[right_id]
                and _compatible_values(
                    player_by_id[left_id].position,
                    player_by_id[right_id].position,
                    positions=True,
                )
                for index, left_id in enumerate(ordered_ids)
                for right_id in ordered_ids[index + 1 :]
            )
            if not compatible_group:
                ambiguous_global_groups.append(sorted(candidate_ids))
                continue
            for player_id in candidate_ids:
                graph[player_id].update(candidate_ids - {player_id})

    # Conservative alias matching: same active club, exact DOB, compatible
    # nationality/position, and a close spelling or transliteration.
    for players_in_team in same_birth_groups.values():
        players_list = list(players_in_team.values())
        for index, left in enumerate(players_list):
            left_name = normalize_name(left.display_name)
            for right in players_list[index + 1 :]:
                right_name = normalize_name(right.display_name)
                if left_name == right_name:
                    continue
                if SequenceMatcher(None, left_name, right_name).ratio() < 0.80:
                    continue
                if not _compatible_values(left.nationality, right.nationality):
                    continue
                if not _compatible_values(left.position, right.position, positions=True):
                    continue
                graph[left.id].add(right.id)
                graph[right.id].add(left.id)

    components: list[set[int]] = []
    visited: set[int] = set()
    for start in graph:
        if start in visited:
            continue
        todo = [start]
        component: set[int] = set()
        while todo:
            player_id = todo.pop()
            if player_id in component:
                continue
            component.add(player_id)
            visited.add(player_id)
            todo.extend(graph[player_id] - component)
        components.append(component)

    all_candidates = (
        set(graph)
        | NICO_IDS
        | KAIKI_IDS
        | KEHRER_IDS
        | ISAK_JENSEN_IDS
        | OSO_IDS
        | MIKKEL_BRO_HANSEN_IDS
        | SERGIO_MARTINEZ_IDS
        | OMOBAMIDELE_IDS
        | FOFANA_IDS
        | {
            player_id
            for canonical_id, duplicate_id, _, _ in VERIFIED_CROSS_PROVIDER_DUPLICATES
            for player_id in (canonical_id, duplicate_id)
        }
    )
    players = {
        player.id: player
        for player in session.scalars(select(Player).where(Player.id.in_(all_candidates)))
    }
    present_nico_ids = NICO_IDS & players.keys()
    if NICO_CANONICAL_ID not in present_nico_ids:
        raise RuntimeError("No se encontró el perfil canónico de Nico Paz.")
    if len(present_nico_ids) > 1 and players[NICO_CANONICAL_ID].birth_date != VERIFIED_DOBS[NICO_CANONICAL_ID]:
        raise RuntimeError("La fecha del perfil canónico de Nico Paz no coincide con la verificada.")
    accepted_nico_names = {"nico paz", "nicolas paz", "nicolas paz martinez"}
    if any(normalize_name(players[i].display_name) not in accepted_nico_names for i in present_nico_ids):
        raise RuntimeError("Los perfiles de Nico Paz cambiaron; se cancela la consolidación manual.")
    common_memberships = (
        set.intersection(*(player_current_clubs[i] for i in present_nico_ids))
        if len(present_nico_ids) > 1
        else set()
    )
    if len(present_nico_ids) > 1 and not common_memberships:
        raise RuntimeError("Los perfiles de Nico Paz no comparten equipo y temporada activos.")

    present_kaiki_ids = KAIKI_IDS & players.keys()
    if KAIKI_CANONICAL_ID not in present_kaiki_ids:
        raise RuntimeError("No se encontró el perfil canónico de Kaiki.")
    if any(normalize_name(players[i].display_name) not in KAIKI_ACCEPTED_NAMES for i in present_kaiki_ids):
        raise RuntimeError("Los perfiles de Kaiki cambiaron; se cancela la consolidación manual.")
    kaiki_common_memberships = (
        set.intersection(*(player_current_clubs[i] for i in present_kaiki_ids))
        if len(present_kaiki_ids) > 1
        else set()
    )
    if len(present_kaiki_ids) > 1 and not kaiki_common_memberships:
        raise RuntimeError("Los perfiles de Kaiki no comparten club y temporada activos.")

    present_kehrer_ids = KEHRER_IDS & players.keys()
    if KEHRER_CANONICAL_ID not in present_kehrer_ids:
        raise RuntimeError("No se encontró el perfil canónico de Thilo Kehrer.")
    if len(present_kehrer_ids) > 1 and players[KEHRER_CANONICAL_ID].birth_date != date(1996, 9, 21):
        raise RuntimeError("La fecha del perfil canónico de Thilo Kehrer no coincide con la verificada.")
    if any(normalize_name(players[i].display_name) not in KEHRER_ACCEPTED_NAMES for i in present_kehrer_ids):
        raise RuntimeError("Los perfiles de Thilo Kehrer cambiaron; se cancela la consolidación manual.")
    kehrer_common_memberships = (
        set.intersection(*(player_current_clubs[i] for i in present_kehrer_ids))
        if len(present_kehrer_ids) > 1
        else set()
    )
    if len(present_kehrer_ids) > 1 and not kehrer_common_memberships:
        raise RuntimeError("Los perfiles de Thilo Kehrer no comparten club y temporada activos.")

    present_isak_ids = ISAK_JENSEN_IDS & players.keys()
    if ISAK_JENSEN_CANONICAL_ID not in present_isak_ids:
        raise RuntimeError("No se encontró el perfil canónico de Isak Jensen.")
    if len(present_isak_ids) > 1 and players[ISAK_JENSEN_CANONICAL_ID].birth_date != date(2003, 12, 23):
        raise RuntimeError("La fecha del perfil canónico de Isak Jensen no coincide con la verificada.")
    if any(normalize_name(players[i].display_name) != "isak jensen" for i in present_isak_ids):
        raise RuntimeError("Los perfiles de Isak Jensen cambiaron; se cancela la consolidación manual.")

    present_oso_ids = OSO_IDS & players.keys()
    if OSO_CANONICAL_ID not in present_oso_ids:
        raise RuntimeError("No se encontró el perfil canónico de Oso.")
    if len(present_oso_ids) > 1 and players[OSO_CANONICAL_ID].birth_date != date(2003, 7, 9):
        raise RuntimeError("La fecha del perfil canónico de Oso no coincide con la verificada.")
    if any(normalize_name(players[i].display_name) not in {"oso", "joaquin martinez gauna"} for i in present_oso_ids):
        raise RuntimeError("Los perfiles de Oso cambiaron; se cancela la consolidación manual.")

    present_mikkel_ids = MIKKEL_BRO_HANSEN_IDS & players.keys()
    if MIKKEL_BRO_HANSEN_CANONICAL_ID not in present_mikkel_ids:
        raise RuntimeError("No se encontró el perfil canónico de Mikkel Bro Hansen.")
    if len(present_mikkel_ids) > 1 and players[MIKKEL_BRO_HANSEN_CANONICAL_ID].birth_date != date(2009, 1, 25):
        raise RuntimeError("La fecha del perfil canónico de Mikkel Bro Hansen no coincide con PSV.")
    if any(normalize_name(players[i].display_name) not in {"mikkel hansen", "mikkel bro hansen"} for i in present_mikkel_ids):
        raise RuntimeError("Los perfiles de Mikkel Bro Hansen cambiaron; se cancela la consolidación manual.")

    present_sergio_ids = SERGIO_MARTINEZ_IDS & players.keys()
    if SERGIO_MARTINEZ_CANONICAL_ID not in present_sergio_ids:
        raise RuntimeError("No se encontró el perfil canónico de Sergio Martínez Montero.")
    if len(present_sergio_ids) > 1 and players[SERGIO_MARTINEZ_CANONICAL_ID].birth_date != date(2007, 5, 15):
        raise RuntimeError("La fecha del perfil canónico de Sergio Martínez Montero no coincide con la verificada.")
    if any(normalize_name(players[i].display_name) not in {"sergio martinez", "sergio martinez montero"} for i in present_sergio_ids):
        raise RuntimeError("Los perfiles de Sergio Martínez cambiaron; se cancela la consolidación manual.")

    present_omobamidele_ids = OMOBAMIDELE_IDS & players.keys()
    if OMOBAMIDELE_CANONICAL_ID not in present_omobamidele_ids:
        raise RuntimeError("No se encontró el perfil canónico de Andrew Omobamidele.")
    if len(present_omobamidele_ids) > 1 and players[OMOBAMIDELE_CANONICAL_ID].birth_date != VERIFIED_DOBS[OMOBAMIDELE_CANONICAL_ID]:
        raise RuntimeError("La fecha del perfil de Andrew Omobamidele no coincide con UEFA.")
    if any(
        normalize_name(players[i].display_name)
        not in {"andrew omobamidele", "andrew abiola omobamidele"}
        for i in present_omobamidele_ids
    ):
        raise RuntimeError("Los perfiles de Andrew Omobamidele cambiaron; se cancela la consolidación manual.")
    omobamidele_common_memberships = (
        set.intersection(*(player_current_clubs[i] for i in present_omobamidele_ids))
        if len(present_omobamidele_ids) > 1
        else set()
    )
    if len(present_omobamidele_ids) > 1 and not omobamidele_common_memberships:
        raise RuntimeError("Los perfiles de Andrew Omobamidele no comparten club y temporada activos.")

    present_fofana_ids = FOFANA_IDS & players.keys()
    if FOFANA_CANONICAL_ID not in present_fofana_ids:
        raise RuntimeError("No se encontró el perfil canónico de Malick Fofana.")
    if len(present_fofana_ids) > 1 and players[FOFANA_CANONICAL_ID].birth_date != VERIFIED_DOBS[FOFANA_CANONICAL_ID]:
        raise RuntimeError("La fecha del perfil de Malick Fofana no coincide con UEFA.")
    if any(
        normalize_name(players[i].display_name)
        not in {"malick fofana", "malick martin fofana"}
        for i in present_fofana_ids
    ):
        raise RuntimeError("Los perfiles de Malick Fofana cambiaron; se cancela la consolidación manual.")
    fofana_common_memberships = (
        set.intersection(*(player_current_clubs[i] for i in present_fofana_ids))
        if len(present_fofana_ids) > 1
        else set()
    )
    if len(present_fofana_ids) > 1 and not fofana_common_memberships:
        raise RuntimeError("Los perfiles de Malick Fofana no comparten club y temporada activos.")

    # Score by complete identity and useful stats; strongest profile wins while
    # missing fields are filled from the other rows.
    candidate_ids = (
        set(graph)
        | KAIKI_IDS
        | NICO_IDS
        | KEHRER_IDS
        | ISAK_JENSEN_IDS
        | OSO_IDS
        | MIKKEL_BRO_HANSEN_IDS
        | SERGIO_MARTINEZ_IDS
        | OMOBAMIDELE_IDS
        | FOFANA_IDS
        | {
            player_id
            for canonical_id, duplicate_id, _, _ in VERIFIED_CROSS_PROVIDER_DUPLICATES
            for player_id in (canonical_id, duplicate_id)
        }
    )
    source_rows = _rows(session, SourcePlayer, candidate_ids)
    team_rows = _rows(session, PlayerTeamSeason, candidate_ids)
    season_rows = _rows(session, PlayerCompetitionSeasonStatistic, candidate_ids)
    match_rows = _rows(session, PlayerMatchStatistic, candidate_ids)
    source_count = Counter(row.player_id for row in source_rows)
    team_count = Counter(row.player_id for row in team_rows)
    season_richness: Counter[int] = Counter()
    for row in season_rows:
        season_richness[row.player_id] += sum(
            getattr(row, col.name) is not None
            for col in row.__table__.columns
            if col.name not in {"id", "player_id", "competition_season_id", "source_id"}
        )
    match_richness: Counter[int] = Counter()
    for row in match_rows:
        match_richness[row.player_id] += sum(
            getattr(row, col.name) is not None
            for col in row.__table__.columns
            if col.name not in {"id", "player_id", "match_id", "team_id", "source_id"}
        )
    match_count = Counter(row.player_id for row in match_rows)
    reliability = {1: 4, 7: 3, 10: 3, 9: 2, 12: 2, 5: 1, 6: 1}

    def score(player_id: int) -> tuple[int, int, int, int, int, int, int]:
        player = players[player_id]
        filled = sum(getattr(player, field) is not None for field in PROFILE_FIELDS)
        return (
            filled,
            match_richness[player_id],
            match_count[player_id],
            season_richness[player_id],
            source_count[player_id],
            team_count[player_id],
            reliability.get(player.profile_source_id or -1, 0) * 100000 - player_id,
        )

    plan: list[tuple[int, set[int], str]] = []
    if len(present_nico_ids) > 1:
        plan.append(
            (NICO_CANONICAL_ID, present_nico_ids - {NICO_CANONICAL_ID}, "grupo confirmado: Nico Paz / Nicolás Paz")
        )
    if len(present_kaiki_ids) > 1:
        plan.append(
            (KAIKI_CANONICAL_ID, present_kaiki_ids - {KAIKI_CANONICAL_ID}, "perfil oficial CBF + plantel oficial de Como; alias Kaiki")
        )
    if len(present_kehrer_ids) > 1:
        plan.append(
            (KEHRER_CANONICAL_ID, present_kehrer_ids - {KEHRER_CANONICAL_ID}, "perfil oficial Ajax: Thilo Kehrer, nacido el 21/09/1996; alias Jan Thilo Kehrer")
        )
    if len(present_isak_ids) > 1:
        plan.append(
            (ISAK_JENSEN_CANONICAL_ID, present_isak_ids - {ISAK_JENSEN_CANONICAL_ID}, "AZ confirma el préstamo del mismo Isak Jensen a Lorient; ficha oficial PSV/AZ, nacido el 23/12/2003")
        )
    if len(present_oso_ids) > 1:
        plan.append(
            (OSO_CANONICAL_ID, present_oso_ids - {OSO_CANONICAL_ID}, "Sevilla confirma el traspaso de Joaquín Martínez Gauna (Oso) a Strasbourg")
        )
    if len(present_mikkel_ids) > 1:
        plan.append(
            (MIKKEL_BRO_HANSEN_CANONICAL_ID, present_mikkel_ids - {MIKKEL_BRO_HANSEN_CANONICAL_ID}, "Bodø/Glimt y PSV confirman el mismo traspaso; ficha oficial PSV, nacido el 25/01/2009")
        )
    if len(present_sergio_ids) > 1:
        plan.append(
            (SERGIO_MARTINEZ_CANONICAL_ID, present_sergio_ids - {SERGIO_MARTINEZ_CANONICAL_ID}, "Real Madrid confirma que Sergio Martínez Montero pasó de Racing a Castilla; ficha oficial, nacido el 15/05/2007")
        )
    if len(present_omobamidele_ids) > 1:
        plan.append(
            (OMOBAMIDELE_CANONICAL_ID, present_omobamidele_ids - {OMOBAMIDELE_CANONICAL_ID}, "UEFA verifica fecha de nacimiento; Anderlecht confirma préstamo de Strasbourg: Andrew Omobamidele / Andrew Abiola Omobamidele")
        )
    if len(present_fofana_ids) > 1:
        plan.append(
            (FOFANA_CANONICAL_ID, present_fofana_ids - {FOFANA_CANONICAL_ID}, "UEFA verifica fecha de nacimiento y OL confirma el traspaso de Malick Fofana a Sunderland; alias Malick Martin Fofana")
        )
    verified_cross_provider_ids: set[int] = set()
    source_codes = {
        source.id: source.code
        for source in session.scalars(select(DataSource))
    }
    for canonical_id, duplicate_id, expected_name, expected_dob in VERIFIED_CROSS_PROVIDER_DUPLICATES:
        pair = {canonical_id, duplicate_id}
        if canonical_id not in players:
            raise RuntimeError(f"Falta el perfil canónico confirmado de {expected_name}.")
        # Idempotent after a successful --apply: the duplicate profile has
        # already been removed and its provider/stat rows now point to canonical.
        if duplicate_id not in players:
            verified_cross_provider_ids.add(canonical_id)
            continue
        canonical = players[canonical_id]
        duplicate = players[duplicate_id]
        if (
            normalize_name(canonical.display_name) != normalize_name(expected_name)
            or normalize_name(duplicate.display_name) != normalize_name(expected_name)
            or canonical.birth_date != expected_dob
            or duplicate.birth_date != expected_dob
        ):
            raise RuntimeError(f"Nombre o fecha de nacimiento cambiaron para {expected_name}; no se fusiona.")
        if "espn" not in source_codes.get(canonical.profile_source_id or -1, ""):
            raise RuntimeError(f"El perfil canónico de {expected_name} ya no proviene de ESPN.")
        if "highlightly" not in source_codes.get(duplicate.profile_source_id or -1, ""):
            raise RuntimeError(f"El perfil duplicado de {expected_name} ya no proviene de Highlightly.")
        if not _compatible_values(canonical.position, duplicate.position, positions=True):
            raise RuntimeError(f"Las posiciones de {expected_name} dejaron de ser compatibles.")
        memberships_for_canonical = player_current_clubs[canonical_id]
        if not memberships_for_canonical:
            raise RuntimeError(f"El perfil canónico de {expected_name} no tiene club activo.")
        overlap = session.execute(
            select(PlayerMatchStatistic.id)
            .join(Match, Match.id == PlayerMatchStatistic.match_id)
            .where(
                PlayerMatchStatistic.player_id == duplicate_id,
                PlayerMatchStatistic.team_id.in_({team_id for team_id, _ in memberships_for_canonical}),
                Match.competition_season_id.in_({season_id for _, season_id in memberships_for_canonical}),
                Match.status == "FINISHED",
                Match.home_score_ft.is_not(None),
                Match.away_score_ft.is_not(None),
            )
            .limit(1)
        ).first()
        if overlap is None:
            raise RuntimeError(f"No hay partido oficial reciente que vincule {expected_name} con su plantel ESPN.")
        plan.append((canonical_id, {duplicate_id}, f"duplicado confirmado: fecha exacta, identidades ESPN/Highlightly y partido oficial con el club activo ({expected_name})"))
        verified_cross_provider_ids.update(pair)
    used = (
        set(present_nico_ids)
        | present_kaiki_ids
        | present_kehrer_ids
        | present_isak_ids
        | present_oso_ids
        | present_mikkel_ids
        | present_sergio_ids
        | present_omobamidele_ids
        | present_fofana_ids
        | verified_cross_provider_ids
    )
    unresolved: list[list[int]] = [
        ids for ids in ambiguous_global_groups
        if not set(ids) <= verified_cross_provider_ids
    ]
    for component in components:
        if component & used:
            if component - used:
                raise RuntimeError("Un grupo automático se superpone con el grupo manual de Nico Paz.")
            continue
        dob_values = {players[i].birth_date for i in component if players[i].birth_date is not None}
        verified = component & VERIFIED_DOBS.keys()
        if len(dob_values) > 1:
            if len(verified) != 1:
                unresolved.append(sorted(component))
                continue
            canonical_id = next(iter(verified))
            canonical = players[canonical_id]
            if normalize_name(canonical.display_name) != normalize_name(players[next(iter(component))].display_name):
                unresolved.append(sorted(component))
                continue
            plan.append((canonical_id, component - {canonical_id}, "identidad/DOB verificados por fuente oficial"))
            used.update(component)
            continue
        canonical_id = max(component, key=score)
        plan.append((canonical_id, component - {canonical_id}, "mismo nombre normalizado y equipo/temporada activos"))
        used.update(component)

    duplicate_to_canonical: dict[int, int] = {}
    for canonical_id, duplicate_ids, _ in plan:
        for duplicate_id in duplicate_ids:
            if duplicate_id in duplicate_to_canonical:
                raise RuntimeError(f"El jugador duplicado {duplicate_id} fue asignado más de una vez.")
            duplicate_to_canonical[duplicate_id] = canonical_id
    return plan, unresolved


def backup_rows(session: Any, player_ids: set[int], plan: list[tuple[int, set[int], str]]) -> Path:
    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"player-dedup-{stamp}.json.gz"
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "database": get_settings().database_name,
        "plan": [
            {"canonical_id": canonical, "duplicate_ids": sorted(duplicates), "evidence": evidence}
            for canonical, duplicates, evidence in plan
        ],
        "tables": {},
    }
    models = (
        Player,
        SourcePlayer,
        PlayerTeamSeason,
        PlayerCompetitionSeasonStatistic,
        PlayerMatchStatistic,
        MatchEvent,
    )
    for model in models:
        id_field = model.id if model is Player else model.player_id
        payload["tables"][model.__tablename__] = [
            _row_dict(row)
            for row in session.scalars(select(model).where(id_field.in_(player_ids)))
        ]
    with gzip.open(destination, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, default=_json_default)
    return destination


def _merge_child_rows(session: Any, model: Any, key_fields: tuple[str, ...], mapping: dict[int, int], conflicts: Counter[str]) -> int:
    old_ids = set(mapping)
    all_ids = old_ids | set(mapping.values())
    rows = _rows(session, model, all_ids)
    groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in rows:
        target = mapping.get(row.player_id, row.player_id)
        # SourcePlayer's unique key is provider/external ID and intentionally
        # does not contain player_id; the other tables include target player ID.
        prefix = () if model is SourcePlayer else (target,)
        groups[prefix + tuple(getattr(row, key) for key in key_fields)].append(row)

    reassign: list[tuple[Any, int]] = []
    removed = 0
    for key, group in groups.items():
        target = mapping.get(group[0].player_id, group[0].player_id)
        if len(group) == 1:
            if group[0].player_id != target:
                reassign.append((group[0], target))
            continue

        if model is SourcePlayer:
            raise RuntimeError(f"Colisión inesperada de IDs de origen en {model.__tablename__}: {key}")

        def richness(row: Any) -> tuple[int, datetime, int]:
            filled = sum(
                getattr(row, column.name) is not None
                for column in row.__table__.columns
                if column.name not in {"id", "player_id", *key_fields}
            )
            updated = getattr(row, "source_updated_at", None) or datetime.min.replace(tzinfo=UTC)
            return filled, updated, row.id

        winner = max(group, key=richness)
        for row in sorted(group, key=richness, reverse=True):
            if row is winner:
                continue
            for column in row.__table__.columns:
                name = column.name
                if name in {"id", "player_id", *key_fields}:
                    continue
                current, other = getattr(winner, name), getattr(row, name)
                if current is None and other is not None:
                    setattr(winner, name, other)
                elif current is not None and other is not None and current != other:
                    conflicts[f"{model.__tablename__}.{name}"] += 1
            session.delete(row)
            removed += 1
        if winner.player_id != target:
            reassign.append((winner, target))

    # Delete colliding rows before changing a duplicate's key to the canonical
    # player, keeping PostgreSQL unique constraints valid throughout the flush.
    session.flush()
    for row, target in reassign:
        row.player_id = target
    session.flush()
    return removed


def run(apply: bool) -> None:
    factory = make_session_factory()
    conflict_counts: Counter[str] = Counter()
    with factory() as session, session.begin():
        plan, unresolved = build_plan(session)
        duplicate_to_canonical = {
            duplicate: canonical
            for canonical, duplicates, _ in plan
            for duplicate in duplicates
        }
        duplicate_ids = set(duplicate_to_canonical)
        affected_ids = duplicate_ids | set(duplicate_to_canonical.values())
        print(f"Jugadores duplicados para consolidar: {len(duplicate_ids)}")
        print(f"Grupos que se consolidan: {len(plan)}")
        print(f"Grupos ambiguos que se conservan intactos: {len(unresolved)}")
        if plan:
            print("Detalle de grupos confirmados:")
        for canonical_id, duplicate_ids_in_group, evidence in plan:
            ids = {canonical_id, *duplicate_ids_in_group}
            group_players = session.scalars(select(Player).where(Player.id.in_(ids))).all()
            memberships = session.execute(
                select(PlayerTeamSeason, Team)
                .join(Team, Team.id == PlayerTeamSeason.team_id)
                .where(
                    PlayerTeamSeason.player_id.in_(ids),
                    PlayerTeamSeason.active.is_(True),
                )
                .order_by(Team.canonical_name, PlayerTeamSeason.player_id)
            ).all()
            print(f"  Evidencia: {evidence}")
            for player in sorted(group_players, key=lambda row: row.id):
                player_memberships = [
                    f"{team.canonical_name} (temporada competición {membership.competition_season_id})"
                    for membership, team in memberships
                    if membership.player_id == player.id
                ]
                child_counts = {
                    "fuentes": session.scalar(
                        select(func.count()).select_from(SourcePlayer).where(
                            SourcePlayer.player_id == player.id
                        )
                    ),
                    "planteles": session.scalar(
                        select(func.count()).select_from(PlayerTeamSeason).where(
                            PlayerTeamSeason.player_id == player.id
                        )
                    ),
                    "stats_temporada": session.scalar(
                        select(func.count()).select_from(PlayerCompetitionSeasonStatistic).where(
                            PlayerCompetitionSeasonStatistic.player_id == player.id
                        )
                    ),
                    "stats_partido": session.scalar(
                        select(func.count()).select_from(PlayerMatchStatistic).where(
                            PlayerMatchStatistic.player_id == player.id
                        )
                    ),
                    "eventos": session.scalar(
                        select(func.count()).select_from(MatchEvent).where(
                            MatchEvent.player_id == player.id
                        )
                    ),
                }
                print(
                    f"    ID {player.id}: {player.display_name}; nacimiento={player.birth_date}; "
                    f"nacionalidad={player.nationality}; posición={player.position}; "
                    f"fuente_perfil={player.profile_source_id}; clubes_activos={player_memberships}; "
                    f"filas={child_counts}"
                )
        for ids in unresolved:
            print("  pendiente:", ", ".join(f"{i} {session.get(Player, i).display_name}" for i in ids))
        print("Nico Paz: perfil canónico ID 5380; IDs duplicados pendientes:", sorted(NICO_IDS & duplicate_ids))
        print("Kaiki: perfil canónico ID 5369; IDs duplicados pendientes:", sorted(KAIKI_IDS & duplicate_ids))
        print("Thilo Kehrer: perfil canónico ID 3995; IDs duplicados pendientes:", sorted(KEHRER_IDS & duplicate_ids))
        print("Isak Jensen: perfil canónico ID 5725; IDs duplicados pendientes:", sorted(ISAK_JENSEN_IDS & duplicate_ids))
        print("Joaquín Martínez Gauna (Oso): perfil canónico ID 7021; IDs duplicados pendientes:", sorted(OSO_IDS & duplicate_ids))
        print("Mikkel Bro Hansen: perfil canónico ID 4337; IDs duplicados pendientes:", sorted(MIKKEL_BRO_HANSEN_IDS & duplicate_ids))
        print("Sergio Martínez Montero: perfil canónico ID 9510; IDs duplicados pendientes:", sorted(SERGIO_MARTINEZ_IDS & duplicate_ids))
        if not apply:
            print("Simulación solamente; no se guardó ningún cambio.")
            return

        backup_path = backup_rows(session, affected_ids, plan)
        print(f"Respaldo creado: {backup_path}")

        players = {
            row.id: row
            for row in session.scalars(select(Player).where(Player.id.in_(affected_ids)))
        }
        for canonical_id, duplicate_ids_in_group, _ in plan:
            canonical = players[canonical_id]
            duplicates = [players[i] for i in duplicate_ids_in_group]
            for field in PROFILE_FIELDS:
                if getattr(canonical, field) is None:
                    sources = [p for p in duplicates if getattr(p, field) is not None]
                    if sources:
                        source = max(sources, key=lambda p: sum(getattr(p, f) is not None for f in PROFILE_FIELDS))
                        setattr(canonical, field, getattr(source, field))
                for duplicate in duplicates:
                    value = getattr(duplicate, field)
                    kept = getattr(canonical, field)
                    if value is not None and kept is not None and value != kept:
                        conflict_counts[f"players.{field}"] += 1
            timestamps = [p.profile_updated_at for p in [canonical, *duplicates] if p.profile_updated_at]
            if timestamps:
                canonical.profile_updated_at = max(timestamps)
            canonical.updated_at = datetime.now(UTC)
        for player_id, verified_dob in VERIFIED_DOBS.items():
            if player_id in players and player_id in duplicate_to_canonical.values():
                players[player_id].birth_date = verified_dob
        if KAIKI_CANONICAL_ID in players:
            kaiki = players[KAIKI_CANONICAL_ID]
            if kaiki.birth_date != KAIKI_VERIFIED_DOB:
                conflict_counts["players.birth_date (Kaiki, corrección CBF)"] += 1
            kaiki.display_name = KAIKI_CANONICAL_NAME
            kaiki.birth_date = KAIKI_VERIFIED_DOB
            if kaiki.nationality is None:
                kaiki.nationality = "Brazil"

        removed_child_rows = {}
        for model, key_fields in UNIQUE_CHILDREN:
            removed_child_rows[model.__tablename__] = _merge_child_rows(
                session, model, key_fields, duplicate_to_canonical, conflict_counts
            )

        for duplicate_id, canonical_id in duplicate_to_canonical.items():
            session.query(MatchEvent).filter(MatchEvent.player_id == duplicate_id).update(
                {MatchEvent.player_id: canonical_id}, synchronize_session=False
            )
        session.flush()

        # Every known FK to players was audited before the transaction. Reject
        # the operation if any child reference still points at a duplicate.
        for model in (SourcePlayer, PlayerTeamSeason, PlayerCompetitionSeasonStatistic, PlayerMatchStatistic, MatchEvent):
            remaining = session.scalar(
                select(func.count()).select_from(model).where(model.player_id.in_(duplicate_ids))
            )
            if remaining:
                raise RuntimeError(f"Quedaron {remaining} referencias en {model.__tablename__}; se revierte todo.")
        for duplicate_id in duplicate_ids:
            session.delete(players[duplicate_id])
        session.flush()
        survivors = session.scalar(select(func.count()).select_from(Player).where(Player.id.in_(duplicate_ids)))
        if survivors:
            raise RuntimeError(f"Quedaron {survivors} jugadores duplicados; se revierte todo.")

        print(f"Filas hijas fusionadas/eliminadas por colisión: {removed_child_rows}")
        print(f"Campos con valores distintos preservados en el respaldo: {dict(conflict_counts)}")
        print("Validación FK previa al commit: sin referencias a IDs duplicados.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="aplica la fusión tras crear respaldo")
    run(parser.parse_args().apply)
