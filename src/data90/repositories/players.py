"""Persistence and queries for provider-backed player statistics."""

import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from data90.config.constants import COMPETITIONS
from data90.domain.enums import MatchStatus
from data90.database.models import (
    Competition,
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


# These two Shakhtar players share the nickname "Pedrinho" but are different
# people. Keep their verified full names stable when a provider sends only the
# nickname, so the squad and global search do not look like duplicate rows.
VERIFIED_PLAYER_DISPLAY_NAMES = {
    5941: "Pedro Henrique Azevedo Pereira",
    5953: "Pedro Victor Delmino da Silva",
    3170: "Alexandre Zurawski",
    6281: "João Tornich",
    4977: "Éderson José dos Santos Lourenço da Silva",
    5844: "Ederson Santana de Moraes",
    9433: "João Paulo Santos da Costa",
    10174: "João José Pereira da Costa",
    3781: "Otávio Ataíde da Silva",
    5483: "Otávio Manoel Galdino Fernandes",
    5246: "Raúl García de Haro",
    7087: "Raúl Asencio del Rosario",
    7345: "Rui Tiago Dantas da Silva",
    9660: "Rui Pedro Ferreira da Silva",
    4337: "Mikkel Bro Hansen",
    7021: "Joaquín Martínez Gauna",
    9510: "Sergio Martínez Montero",
}
from data90.domain.dto import NormalizedPlayerSeasonStatistic

_LOADED_COMPETITION_CODES = tuple(item.code for item in COMPETITIONS)


@dataclass(frozen=True, slots=True)
class PlayerSeasonItem:
    player_id: int
    display_name: str
    competition_season_id: int
    season_label: str
    goals: int | None
    assists: int | None
    appearances: int | None
    source_code: str


@dataclass(frozen=True, slots=True)
class PlayerDirectoryItem:
    player_id: int
    display_name: str
    nationality: str | None
    position: str | None
    preferred_foot: str | None
    birth_date: date | None
    shirt_number: int | None
    team_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlayerGoalEventItem:
    kickoff_utc: datetime
    home_team: str
    away_team: str
    minute: int | None
    player_name: str
    team_name: str | None
    event_type: str
    source_code: str
    player_id: int | None


@dataclass(frozen=True, slots=True)
class PlayerSeasonDetail:
    competition_season_id: int
    competition_name: str
    season_label: str
    appearances: int | None
    minutes_played: int | None
    goals: int | None
    assists: int | None
    penalties_scored: int | None
    yellow_cards: int | None
    red_cards: int | None
    tackles_won: int | None
    starts: int | None
    substitute_appearances: int | None
    shots_total: int | None
    shots_on_target: int | None
    goals_conceded: int | None
    clean_sheets: int | None
    sources_by_field: dict[str, str]
    updated_by_field: dict[str, datetime | None]
    venue_stats: dict[str, int | None]
    venue_appearances_with_detail: dict[str, int]
    venue_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlayerTeamMembership:
    competition_season_id: int
    team_id: int
    team_name: str
    competition_name: str
    season_label: str
    position: str | None
    shirt_number: int | None
    crest_url: str | None = None


@dataclass(frozen=True, slots=True)
class PlayerProfileDetail:
    player_id: int
    display_name: str
    nationality: str | None
    position: str | None
    preferred_foot: str | None
    birth_date: date | None
    height: str | None
    weight: str | None
    profile_source_code: str | None
    profile_updated_at: datetime | None
    team_competitions: tuple[PlayerTeamMembership, ...]
    statistics: tuple[PlayerSeasonDetail, ...]


def player_profile_detail(session: Session, player_id: int) -> PlayerProfileDetail | None:
    player = session.get(Player, player_id)
    if player is None:
        return None
    memberships = session.execute(
        select(
            CompetitionSeason.id,
            Team.id,
            Team.canonical_name,
            Competition.name,
            Season.label,
            PlayerTeamSeason.position,
            PlayerTeamSeason.shirt_number,
            Team.crest_url,
        )
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .join(
            CompetitionSeason,
            CompetitionSeason.id == PlayerTeamSeason.competition_season_id,
        )
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .where(
            PlayerTeamSeason.player_id == player_id,
            PlayerTeamSeason.active.is_(True),
            Competition.code.in_(_LOADED_COMPETITION_CODES),
        )
        .order_by(Season.start_year.desc(), Competition.display_order, Team.canonical_name)
    ).all()
    team_competitions = tuple(PlayerTeamMembership(*row) for row in dict.fromkeys(memberships))
    statistics = session.execute(
        select(
            CompetitionSeason.id,
            Competition.name,
            Season.label,
            PlayerCompetitionSeasonStatistic.appearances,
            PlayerCompetitionSeasonStatistic.minutes_played,
            PlayerCompetitionSeasonStatistic.goals,
            PlayerCompetitionSeasonStatistic.assists,
            PlayerCompetitionSeasonStatistic.penalties_scored,
            PlayerCompetitionSeasonStatistic.yellow_cards,
            PlayerCompetitionSeasonStatistic.red_cards,
            PlayerCompetitionSeasonStatistic.tackles_won,
            PlayerCompetitionSeasonStatistic.starts,
            PlayerCompetitionSeasonStatistic.substitute_appearances,
            PlayerCompetitionSeasonStatistic.shots_total,
            PlayerCompetitionSeasonStatistic.shots_on_target,
            PlayerCompetitionSeasonStatistic.goals_conceded,
            PlayerCompetitionSeasonStatistic.clean_sheets,
            DataSource.code,
            DataSource.priority,
            PlayerCompetitionSeasonStatistic.source_updated_at,
        )
        .join(
            CompetitionSeason,
            CompetitionSeason.id == PlayerCompetitionSeasonStatistic.competition_season_id,
        )
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .join(DataSource, DataSource.id == PlayerCompetitionSeasonStatistic.source_id)
        .where(
            PlayerCompetitionSeasonStatistic.player_id == player_id,
            Competition.code.in_(_LOADED_COMPETITION_CODES),
        )
        .order_by(Season.start_year.desc(), Competition.display_order, DataSource.priority)
    ).all()
    player_match_rows = session.execute(
        select(
            CompetitionSeason.id,
            Competition.name,
            Season.label,
            Match.id,
            PlayerMatchStatistic.team_id,
            Match.home_team_id,
            PlayerMatchStatistic.minutes_played,
            PlayerMatchStatistic.is_substitute,
            PlayerMatchStatistic.goals,
            PlayerMatchStatistic.assists,
            PlayerMatchStatistic.yellow_cards,
            PlayerMatchStatistic.red_cards,
            PlayerMatchStatistic.shots_total,
            PlayerMatchStatistic.shots_on_target,
            DataSource.code,
            DataSource.priority,
        )
        .join(Match, Match.id == PlayerMatchStatistic.match_id)
        .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .join(DataSource, DataSource.id == PlayerMatchStatistic.source_id)
        .where(
            PlayerMatchStatistic.player_id == player_id,
            Match.status == MatchStatus.FINISHED.value,
            Competition.code.in_(_LOADED_COMPETITION_CODES),
            or_(
                PlayerMatchStatistic.team_id == Match.home_team_id,
                PlayerMatchStatistic.team_id == Match.away_team_id,
            ),
        )
        .order_by(CompetitionSeason.id, Match.id, DataSource.priority)
    ).all()
    statistic_fields = (
        "appearances",
        "minutes_played",
        "goals",
        "assists",
        "penalties_scored",
        "yellow_cards",
        "red_cards",
        "tackles_won",
        "starts",
        "substitute_appearances",
        "shots_total",
        "shots_on_target",
        "goals_conceded",
        "clean_sheets",
    )
    grouped: dict[int, dict[str, object]] = {}
    for row in statistics:
        season_id = row[0]
        detail = grouped.setdefault(
            season_id,
            {
                "competition_season_id": season_id,
                "competition_name": row[1],
                "season_label": row[2],
                "sources_by_field": {},
                "updated_by_field": {},
                "venue_stats": {},
                "venue_appearances_with_detail": {"home": 0, "away": 0},
                "venue_sources": (),
                **dict.fromkeys(statistic_fields),
            },
        )
        for index, field in enumerate(statistic_fields, start=3):
            value = row[index]
            if value is not None and detail[field] is None:
                detail[field] = value
                detail["sources_by_field"][field] = row[-3]
                detail["updated_by_field"][field] = row[-1]

    match_rows_by_season: dict[int, dict[int, dict[str, object]]] = {}
    match_stat_fields = (
        "minutes_played",
        "is_substitute",
        "goals",
        "assists",
        "yellow_cards",
        "red_cards",
        "shots_total",
        "shots_on_target",
    )
    for row in player_match_rows:
        season_id, competition_name, season_label, match_id, team_id, home_team_id = row[:6]
        matches = match_rows_by_season.setdefault(season_id, {})
        match = matches.setdefault(
            match_id,
            {
                "competition_name": competition_name,
                "season_label": season_label,
                "side": "home" if team_id == home_team_id else "away",
                "fields": {},
                "sources": set(),
            },
        )
        match["sources"].add(row[-2])
        fields = match["fields"]
        for index, field in enumerate(match_stat_fields, start=6):
            value = row[index]
            if value is not None and field not in fields:
                fields[field] = value

    for season_id, matches in match_rows_by_season.items():
        sample = next(iter(matches.values()))
        detail = grouped.setdefault(
            season_id,
            {
                "competition_season_id": season_id,
                "competition_name": sample["competition_name"],
                "season_label": sample["season_label"],
                "sources_by_field": {},
                "updated_by_field": {},
                "venue_stats": {},
                "venue_appearances_with_detail": {"home": 0, "away": 0},
                "venue_sources": (),
                **dict.fromkeys(statistic_fields),
            },
        )
        side_appearances: dict[str, list[dict[str, object]]] = {"home": [], "away": []}
        sources: set[str] = set()
        for match in matches.values():
            fields = match["fields"]
            minutes = fields.get("minutes_played")
            if not isinstance(minutes, int) or minutes <= 0:
                continue
            side_appearances[match["side"]].append(fields)
            sources.update(match["sources"])

        detail["venue_appearances_with_detail"] = {
            side: len(rows) for side, rows in side_appearances.items()
        }
        detail["venue_sources"] = tuple(sorted(sources))
        venue_stats: dict[str, int | None] = {}
        venue_fields = (
            "starts",
            "substitute_appearances",
            "minutes_played",
            "goals",
            "assists",
            "yellow_cards",
            "red_cards",
            "shots_total",
            "shots_on_target",
        )
        for side, rows in side_appearances.items():
            venue_stats[f"{side}_appearances"] = len(rows) if rows else None
            for field in venue_fields:
                if field == "starts":
                    values = [
                        None if row.get("is_substitute") is None else row.get("is_substitute") is False
                        for row in rows
                    ]
                elif field == "substitute_appearances":
                    values = [
                        None if row.get("is_substitute") is None else row.get("is_substitute") is True
                        for row in rows
                    ]
                else:
                    values = [row.get(field) for row in rows]
                venue_stats[f"{side}_{field}"] = (
                    sum(values)
                    if rows and all(value is not None for value in values)
                    else None
                )
        detail["venue_stats"] = venue_stats
        source_label = ", ".join(sorted(sources)) or "Partidos registrados"
        for field in (
            "appearances",
            "goals",
            "assists",
            "yellow_cards",
            "red_cards",
            "shots",
        ):
            detail["sources_by_field"][f"venue_{field}"] = source_label
    season_details = tuple(
        PlayerSeasonDetail(**detail)
        for detail in sorted(
            grouped.values(),
            key=lambda item: (item["season_label"], item["competition_name"]),
            reverse=True,
        )
    )
    return PlayerProfileDetail(
        player.id,
        player.display_name,
        player.nationality,
        player.position,
        player.preferred_foot,
        player.birth_date,
        player.height,
        player.weight,
        session.scalar(select(DataSource.code).where(DataSource.id == player.profile_source_id))
        if player.profile_source_id is not None
        else None,
        player.profile_updated_at,
        team_competitions,
        season_details,
    )


def player_directory(
    session: Session,
    *,
    competition_season_id: int | None = None,
    query: str | None = None,
    limit: int = 5000,
) -> list[PlayerDirectoryItem]:
    """List distinct players in active squads, optionally for one competition season."""
    statement = (
        select(
            Player.id,
            Player.display_name,
            Player.nationality,
            Player.position,
            Player.preferred_foot,
            Player.birth_date,
            PlayerTeamSeason.shirt_number,
            Team.canonical_name,
            Competition.name,
            Season.label,
            CompetitionSeason.id,
            DataSource.priority,
            Team.id,
            SourcePlayer.player_id.is_not(None).label("has_pitchapi_identity"),
        )
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .join(CompetitionSeason, CompetitionSeason.id == PlayerTeamSeason.competition_season_id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .join(DataSource, DataSource.id == PlayerTeamSeason.source_id)
        .outerjoin(
            SourcePlayer,
            (SourcePlayer.player_id == Player.id)
            & (
                SourcePlayer.source_id
                == select(DataSource.id).where(DataSource.code == "pitchapi").scalar_subquery()
            ),
        )
        .where(
            PlayerTeamSeason.active.is_(True),
            Season.active.is_(True),
            Competition.code.in_(_LOADED_COMPETITION_CODES),
        )
        .order_by(
            SourcePlayer.player_id.is_not(None).desc(),
            (DataSource.code != "football-data"),
            DataSource.priority,
            Player.profile_updated_at.desc().nullslast(),
            Player.display_name,
        )
    )
    if competition_season_id is not None:
        statement = statement.where(PlayerTeamSeason.competition_season_id == competition_season_id)
    if query and query.strip():
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(Player.display_name.ilike(pattern), Team.canonical_name.ilike(pattern))
        )
    grouped: dict[tuple[str, int], dict[str, object]] = {}
    for row in session.execute(statement.limit(limit * 4)):
        key = (_normalized_player_name(row[1]), row[12])
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "player_id": row[0],
                "display_name": row[1],
                "nationality": row[2],
                "position": row[3],
                "preferred_foot": row[4],
                "birth_date": row[5],
                "shirt_number": row[6],
                "team_labels": set(),
            }
            grouped[key] = entry
        elif len(row[1]) > len(str(entry["display_name"])):
            entry["display_name"] = row[1]
        if row[13]:
            entry["player_id"] = row[0]
        entry["team_labels"].add(f"{row[7]} · {row[8]} · {row[9]}")
        if entry["shirt_number"] is None and row[6] is not None:
            entry["shirt_number"] = row[6]
        if entry["nationality"] is None and row[2]:
            entry["nationality"] = row[2]
        if entry["position"] is None and row[3]:
            entry["position"] = row[3]
        if entry["preferred_foot"] is None and row[4]:
            entry["preferred_foot"] = row[4]
        if entry["birth_date"] is None and row[5]:
            entry["birth_date"] = row[5]
    result = [
        PlayerDirectoryItem(**{**entry, "team_labels": tuple(sorted(entry["team_labels"]))})
        for entry in grouped.values()
    ]
    return sorted(result, key=lambda item: item.display_name.casefold())[:limit]


def upsert_source_player(
    session: Session,
    *,
    source_code: str,
    external_id: str,
    display_name: str,
) -> tuple[Player, bool, bool]:
    """Upsert a source-scoped identity without inventing season statistics."""
    clean_name = display_name.strip()
    if not external_id.strip() or not clean_name:
        raise ValueError("La fuente devolvió un jugador sin identificador o nombre")
    source_id = session.scalar(select(DataSource.id).where(DataSource.code == source_code))
    if source_id is None:
        raise ValueError(f"Fuente no configurada: {source_code}")
    source_player = session.scalar(
        select(SourcePlayer).where(
            SourcePlayer.source_id == source_id,
            SourcePlayer.external_id == external_id,
        )
    )
    if source_player is None:
        player = Player(display_name=clean_name)
        session.add(player)
        session.flush()
        session.add(SourcePlayer(source_id=source_id, player_id=player.id, external_id=external_id))
        return player, True, False

    player = session.get(Player, source_player.player_id)
    if player is None:
        raise ValueError("La fuente referencia un jugador inexistente")
    clean_name = VERIFIED_PLAYER_DISPLAY_NAMES.get(player.id, clean_name)
    changed = player.display_name != clean_name
    player.display_name = clean_name
    return player, False, changed


def upsert_player_statistic(
    session: Session,
    statistic: NormalizedPlayerSeasonStatistic,
    *,
    competition_season_id: int,
) -> tuple[Player, bool, bool]:
    player, _, player_changed = upsert_source_player(
        session,
        source_code=statistic.source_code,
        external_id=statistic.external_id,
        display_name=statistic.display_name,
    )
    source_id = session.scalar(
        select(DataSource.id).where(DataSource.code == statistic.source_code)
    )
    if source_id is None:
        raise ValueError(f"Fuente no configurada: {statistic.source_code}")

    row = session.scalar(
        select(PlayerCompetitionSeasonStatistic).where(
            PlayerCompetitionSeasonStatistic.player_id == player.id,
            PlayerCompetitionSeasonStatistic.competition_season_id == competition_season_id,
            PlayerCompetitionSeasonStatistic.source_id == source_id,
        )
    )
    inserted = row is None
    updated = player_changed
    if row is None:
        row = PlayerCompetitionSeasonStatistic(
            player_id=player.id,
            competition_season_id=competition_season_id,
            source_id=source_id,
        )
        session.add(row)
    for field in (
        "goals",
        "assists",
        "appearances",
        "minutes_played",
        "penalties_scored",
        "yellow_cards",
        "red_cards",
        "tackles_won",
        "starts",
        "substitute_appearances",
        "shots_total",
        "shots_on_target",
        "goals_conceded",
        "clean_sheets",
    ):
        value = getattr(statistic, field)
        if value is not None and getattr(row, field) != value:
            setattr(row, field, value)
            updated = True
    row.source_updated_at = datetime.now(UTC)
    return player, inserted, updated and not inserted


def player_statistics_for_competition_season(
    session: Session, competition_season_id: int
) -> list[PlayerSeasonItem]:
    rows = session.execute(
        select(
            Player.id,
            Player.display_name,
            CompetitionSeason.id,
            Season.label,
            PlayerCompetitionSeasonStatistic.goals,
            PlayerCompetitionSeasonStatistic.assists,
            PlayerCompetitionSeasonStatistic.appearances,
            DataSource.code,
        )
        .join(
            PlayerCompetitionSeasonStatistic,
            PlayerCompetitionSeasonStatistic.player_id == Player.id,
        )
        .join(
            CompetitionSeason,
            CompetitionSeason.id == PlayerCompetitionSeasonStatistic.competition_season_id,
        )
        .join(Season, Season.id == CompetitionSeason.season_id)
        .join(DataSource, DataSource.id == PlayerCompetitionSeasonStatistic.source_id)
        .where(PlayerCompetitionSeasonStatistic.competition_season_id == competition_season_id)
        .order_by(
            PlayerCompetitionSeasonStatistic.goals.desc().nullslast(),
            Player.display_name,
        )
    ).all()
    return [PlayerSeasonItem(*row) for row in rows]


def scorer_snapshot_player_ids(
    session: Session, competition_season_id: int, source_code: str
) -> set[int]:
    """Return players in a preserved provider scorer snapshot with goals and appearances."""
    rows = session.scalars(
        select(SourcePlayer.player_id)
        .join(DataSource, DataSource.id == SourcePlayer.source_id)
        .join(
            PlayerCompetitionSeasonStatistic,
            (PlayerCompetitionSeasonStatistic.player_id == SourcePlayer.player_id)
            & (PlayerCompetitionSeasonStatistic.source_id == SourcePlayer.source_id),
        )
        .join(
            PlayerTeamSeason,
            (PlayerTeamSeason.player_id == SourcePlayer.player_id)
            & (PlayerTeamSeason.source_id == SourcePlayer.source_id)
            & (
                PlayerTeamSeason.competition_season_id
                == PlayerCompetitionSeasonStatistic.competition_season_id
            ),
        )
        .where(
            DataSource.code == source_code,
            PlayerCompetitionSeasonStatistic.competition_season_id == competition_season_id,
            PlayerCompetitionSeasonStatistic.goals > 0,
            PlayerCompetitionSeasonStatistic.appearances.is_not(None),
            PlayerTeamSeason.active.is_(True),
        )
        .distinct()
    ).all()
    return set(rows)


def goal_events_for_competition_season(
    session: Session, competition_season_id: int
) -> list[PlayerGoalEventItem]:
    home_team = aliased(Team)
    away_team = aliased(Team)
    scoring_team = aliased(Team)
    rows = session.execute(
        select(
            Match.kickoff_utc,
            home_team.canonical_name,
            away_team.canonical_name,
            MatchEvent.minute,
            MatchEvent.player_name,
            scoring_team.canonical_name,
            MatchEvent.event_type,
            DataSource.code,
            MatchEvent.player_id,
        )
        .join(MatchEvent, MatchEvent.match_id == Match.id)
        .join(home_team, home_team.id == Match.home_team_id)
        .join(away_team, away_team.id == Match.away_team_id)
        .outerjoin(scoring_team, scoring_team.id == MatchEvent.team_id)
        .join(DataSource, DataSource.id == MatchEvent.source_id)
        .where(Match.competition_season_id == competition_season_id)
        .order_by(Match.kickoff_utc.desc(), MatchEvent.minute.nullslast(), MatchEvent.id)
    ).all()
    return [PlayerGoalEventItem(*row) for row in rows]


class PlayerRosterItem(NamedTuple):
    team_id: int
    player_id: int
    team_name: str
    player_name: str
    position: str | None
    nationality: str | None
    birth_date: date | None
    height: str | None
    weight: str | None
    shirt_number: int | None
    appearances: int | None
    goals: int | None
    assists: int | None
    source_code: str
    crest_url: str | None = None


class TeamSquadItem(NamedTuple):
    player_id: int
    player_name: str
    nationality: str | None
    position: str | None
    shirt_number: int | None
    source_code: str
    birth_date: date | None


def team_squad_for_team(session: Session, team_id: int) -> list[TeamSquadItem]:
    """Return active-season squad members, preferring richer source records."""
    pitchapi = session.scalar(select(DataSource.id).where(DataSource.code == "pitchapi"))
    rows = session.execute(
        select(
            Player.id,
            Player.display_name,
            Player.nationality,
            PlayerTeamSeason.position,
            Player.position,
            PlayerTeamSeason.shirt_number,
            DataSource.code,
            DataSource.priority,
            Player.birth_date,
            SourcePlayer.player_id.is_not(None),
        )
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .join(CompetitionSeason, CompetitionSeason.id == PlayerTeamSeason.competition_season_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .join(DataSource, DataSource.id == PlayerTeamSeason.source_id)
        .outerjoin(
            SourcePlayer,
            (SourcePlayer.player_id == Player.id)
            & (SourcePlayer.source_id == (pitchapi if pitchapi is not None else -1)),
        )
        .where(
            PlayerTeamSeason.team_id == team_id,
            PlayerTeamSeason.active.is_(True),
            Season.active.is_(True),
        )
        .order_by(
            SourcePlayer.player_id.is_not(None).desc(),
            (DataSource.code != "football-data"),
            Player.nationality.is_(None),
            (PlayerTeamSeason.position.is_(None) & Player.position.is_(None)),
            DataSource.priority,
            Player.id,
            Player.display_name,
        )
    ).all()
    profiles: dict[str, tuple[set[str], set[str]]] = {}
    for name, nationality, position in session.execute(
        select(Player.display_name, Player.nationality, Player.position)
        .where(or_(Player.nationality.is_not(None), Player.position.is_not(None)))
        .order_by(Player.profile_updated_at.desc().nullslast())
    ):
        key = _normalized_player_name(name)
        nationalities, positions = profiles.setdefault(key, (set(), set()))
        if nationality:
            nationalities.add(nationality)
        if position:
            positions.add(position)

    # Providers often return the same player under different IDs. Deduplicate by
    # full name, prefer current roster data, and use a unique historical profile
    # only when its nationality or position is unambiguous.
    players: list[TeamSquadItem] = []
    verified_identity: list[bool] = []
    for (
        player_id,
        name,
        nationality,
        squad_position,
        profile_position,
        number,
        source,
        _,
        birth_date,
        has_pitchapi_identity,
    ) in rows:
        normalized = _normalized_player_name(name)
        historical_nationalities, historical_positions = profiles.get(normalized, (set(), set()))
        candidate = TeamSquadItem(
            player_id,
            name,
            nationality
            or (
                next(iter(historical_nationalities))
                if len(historical_nationalities) == 1
                else None
            ),
            squad_position
            or profile_position
            or (next(iter(historical_positions)) if len(historical_positions) == 1 else None),
            number,
            source,
            birth_date,
        )
        existing_index = next(
            (
                index
                for index, existing in enumerate(players)
                if _same_player_entity(
                    candidate.player_name,
                    candidate.birth_date,
                    existing.player_name,
                    existing.birth_date,
                )
                or (
                    has_pitchapi_identity or verified_identity[index]
                )
                and _same_verified_player_name(
                    candidate.player_name, existing.player_name
                )
            ),
            None,
        )
        if existing_index is None:
            players.append(candidate)
            verified_identity.append(has_pitchapi_identity)
            continue
        existing = players[existing_index]
        if verified_identity[existing_index]:
            full_name = existing.player_name
        elif has_pitchapi_identity:
            full_name = candidate.player_name
        else:
            full_name = max(
                (existing.player_name, candidate.player_name),
                key=lambda value: (len(value.split()), len(value)),
            )
        players[existing_index] = TeamSquadItem(
            candidate.player_id if has_pitchapi_identity else existing.player_id,
            full_name,
            existing.nationality or candidate.nationality,
            existing.position or candidate.position,
            existing.shirt_number or candidate.shirt_number,
            candidate.source_code if has_pitchapi_identity else existing.source_code,
            candidate.birth_date
            if has_pitchapi_identity
            else existing.birth_date or candidate.birth_date,
        )
        verified_identity[existing_index] = (
            verified_identity[existing_index] or has_pitchapi_identity
        )
    return sorted(players, key=lambda player: player.player_name.casefold())


def _same_player_entity(
    first_name: str,
    first_birth_date: date | None,
    second_name: str,
    second_birth_date: date | None,
) -> bool:
    first_normalized = _normalized_player_name(first_name)
    second_normalized = _normalized_player_name(second_name)
    if first_normalized == second_normalized:
        return not (first_birth_date and second_birth_date) or first_birth_date == second_birth_date
    if first_birth_date is None or first_birth_date != second_birth_date:
        return False
    first_tokens = tuple(first_normalized.replace("-", " ").split())
    second_tokens = tuple(second_normalized.replace("-", " ").split())
    shorter, longer = sorted((first_tokens, second_tokens), key=len)
    index = 0
    for token in longer:
        if token == shorter[index]:
            index += 1
            if index == len(shorter):
                return True
    return False


def _same_verified_player_name(first_name: str, second_name: str) -> bool:
    """Match provider-confirmed aliases without relying on conflicting roster DOBs."""
    first = tuple(_normalized_player_name(first_name).replace("-", " ").split())
    second = tuple(_normalized_player_name(second_name).replace("-", " ").split())
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


def _normalized_player_name(name: str) -> str:
    return " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKD", name.casefold())
            if not unicodedata.combining(char)
        ).split()
    )


def player_roster_for_competition_season(
    session: Session, competition_season_id: int
) -> list[PlayerRosterItem]:
    rows = session.execute(
        select(
            Team.id,
            Player.id,
            Team.canonical_name,
            Player.display_name,
            PlayerTeamSeason.position,
            Player.nationality,
            Player.birth_date,
            Player.height,
            Player.weight,
            PlayerTeamSeason.shirt_number,
            PlayerCompetitionSeasonStatistic.appearances,
            PlayerCompetitionSeasonStatistic.goals,
            PlayerCompetitionSeasonStatistic.assists,
            DataSource.code,
            Team.crest_url,
        )
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .join(DataSource, DataSource.id == PlayerTeamSeason.source_id)
        .outerjoin(
            PlayerCompetitionSeasonStatistic,
            (PlayerCompetitionSeasonStatistic.player_id == PlayerTeamSeason.player_id)
            & (
                PlayerCompetitionSeasonStatistic.competition_season_id
                == PlayerTeamSeason.competition_season_id
            )
            & (PlayerCompetitionSeasonStatistic.source_id == PlayerTeamSeason.source_id),
        )
        .where(PlayerTeamSeason.competition_season_id == competition_season_id)
        .order_by(Team.canonical_name, Player.display_name)
    ).all()
    return [PlayerRosterItem(*row) for row in rows]
