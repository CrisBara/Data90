"""Cross-entity search for current teams and players."""

import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from data90.config.constants import COMPETITIONS
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Player,
    PlayerTeamSeason,
    Season,
    SourcePlayer,
    Team,
    TeamAlias,
)

_LOADED_COMPETITION_CODES = tuple(item.code for item in COMPETITIONS)


@dataclass(frozen=True, slots=True)
class GlobalSearchResult:
    kind: str
    entity_id: int
    name: str
    country_or_nationality: str | None
    leagues_or_teams: str
    crest_url: str | None = None
    team_links: tuple[tuple[int, str, str, str | None, str], ...] = ()


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace(
        "\ufffd", "?"
    )


def _contains_name(name: str, query: str) -> bool:
    folded_name = _fold(name)
    folded_query = _fold(query)
    if not folded_query or len(folded_query) > len(folded_name):
        return False
    for start in range(len(folded_name) - len(folded_query) + 1):
        candidate = folded_name[start : start + len(folded_query)]
        if all(
            char == wanted or char == "?"
            for char, wanted in zip(candidate, folded_query, strict=True)
        ):
            return True
    return False


def _same_player(first: str, second: str) -> bool:
    folded_first, folded_second = _fold(first), _fold(second)
    return len(folded_first) == len(folded_second) and all(
        left == right or left == "?" or right == "?"
        for left, right in zip(folded_first, folded_second, strict=True)
    )


def _name_quality(name: str) -> tuple[int, int, int]:
    return ("?" not in _fold(name), len(name.split()), len(name))


def _team_matches(session: Session, query: str, limit: int) -> list[int]:
    current_team_ids = (
        select(Match.home_team_id)
        .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .where(Season.active.is_(True))
        .union(
            select(Match.away_team_id)
            .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .where(Season.active.is_(True))
        )
    )
    candidates = session.scalars(
        select(Team)
        .where(Team.active.is_(True), Team.id.in_(current_team_ids))
        .order_by(Team.canonical_name)
    ).all()
    aliases: dict[int, list[str]] = {}
    for team_id, alias in session.execute(select(TeamAlias.team_id, TeamAlias.alias)):
        aliases.setdefault(team_id, []).append(alias)
    tokens = [
        token
        for token in _fold(query).split()
        if token not in {"fc", "cf", "ac", "sc", "de", "the", "club"}
    ]
    matched = []
    for team in candidates:
        names = [team.canonical_name, team.short_name or "", *aliases.get(team.id, [])]
        folded_names = [_fold(name) for name in names]
        if tokens and all(any(token in name for name in folded_names) for token in tokens):
            matched.append(team.id)
            if len(matched) >= limit:
                break
    return matched


def search_teams_and_players(
    session: Session, query: str, *, limit: int = 100
) -> list[GlobalSearchResult]:
    """Search accented or unaccented names and return deduplicated current rosters."""
    clean_query = query.strip()
    if not clean_query:
        return []
    team_ids = _team_matches(session, clean_query, limit)
    latest_seasons = (
        select(
            CompetitionSeason.competition_id.label("competition_id"),
            func.max(Season.start_year).label("start_year"),
        )
        .join(Season, Season.id == CompetitionSeason.season_id)
        .where(Season.active.is_(True))
        .group_by(CompetitionSeason.competition_id)
        .subquery()
    )
    league_names: dict[int, set[str]] = {}
    if team_ids:
        rows = session.execute(
            select(Team.id, Competition.name)
            .join(Match, or_(Match.home_team_id == Team.id, Match.away_team_id == Team.id))
            .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
            .join(Competition, Competition.id == CompetitionSeason.competition_id)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .join(
                latest_seasons,
                (latest_seasons.c.competition_id == Competition.id)
                & (latest_seasons.c.start_year == Season.start_year),
            )
            .where(Team.id.in_(team_ids))
            .distinct()
        ).all()
        for team_id, league_name in rows:
            league_names.setdefault(team_id, set()).add(league_name)
    results: list[GlobalSearchResult] = []
    if team_ids:
        for team in session.scalars(select(Team).where(Team.id.in_(team_ids))):
            results.append(
                GlobalSearchResult(
                    "Equipo",
                    team.id,
                    team.canonical_name,
                    team.country_name,
                    ", ".join(sorted(league_names.get(team.id, set())))
                    or "Liga actual no disponible",
                    team.crest_url,
                )
            )

    pitchapi_source_id = session.scalar(
        select(DataSource.id).where(DataSource.code == "pitchapi")
    )
    current_players = session.execute(
        select(
            Player.id,
            Player.display_name,
            Player.nationality,
            SourcePlayer.player_id.is_not(None).label("has_pitchapi_identity"),
        )
        .join(PlayerTeamSeason, PlayerTeamSeason.player_id == Player.id)
        .join(CompetitionSeason, CompetitionSeason.id == PlayerTeamSeason.competition_season_id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .outerjoin(
            SourcePlayer,
            (SourcePlayer.player_id == Player.id)
            & (
                SourcePlayer.source_id
                == (pitchapi_source_id if pitchapi_source_id is not None else -1)
            ),
        )
        .where(
            PlayerTeamSeason.active.is_(True),
            Season.active.is_(True),
            Competition.code.in_(_LOADED_COMPETITION_CODES),
        )
        .distinct()
        .order_by(
            SourcePlayer.player_id.is_not(None).desc(),
            Player.display_name,
            Player.id,
        )
    ).all()
    player_rows = [row for row in current_players if _contains_name(row.display_name, clean_query)]
    if not player_rows:
        return results
    player_ids = [row.id for row in player_rows]
    team_rows = session.execute(
        select(
            PlayerTeamSeason.player_id,
            Team.id,
            Team.canonical_name,
            Competition.name,
            Team.crest_url,
            Competition.code,
        )
        .join(Team, Team.id == PlayerTeamSeason.team_id)
        .join(CompetitionSeason, CompetitionSeason.id == PlayerTeamSeason.competition_season_id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .where(
            PlayerTeamSeason.player_id.in_(player_ids),
            PlayerTeamSeason.active.is_(True),
            Season.active.is_(True),
            Competition.code.in_(_LOADED_COMPETITION_CODES),
        )
        .distinct()
    ).all()
    player_teams: dict[int, set[tuple[int, str, str, str | None, str]]] = {}
    for player_id, team_id, team_name, league_name, crest_url, competition_code in team_rows:
        player_teams.setdefault(player_id, set()).add(
            (team_id, team_name, league_name, crest_url, competition_code)
        )

    groups: list[dict[str, object]] = []
    for row in player_rows:
        memberships = player_teams.get(row.id, set())
        team_ids_for_player = {team_id for team_id, *_ in memberships}
        group = next(
            (
                item
                for item in groups
                if _same_player(str(item["name"]), row.display_name)
                and team_ids_for_player.intersection(item["team_ids"])
            ),
            None,
        )
        if group is None:
            groups.append(
                {
                    "id": row.id,
                    "name": row.display_name,
                    "nationality": row.nationality,
                    "team_ids": set(team_ids_for_player),
                    "teams": set(memberships),
                }
            )
            continue
        group["team_ids"].update(team_ids_for_player)
        group["teams"].update(memberships)
        if _name_quality(row.display_name) > _name_quality(str(group["name"])):
            group["name"] = row.display_name
        # Candidate rows are ordered with the enriched PitchAPI identity first.
        # Keep that ID even if a later duplicate has a slightly better label.
        if row.has_pitchapi_identity:
            group["id"] = row.id
        if not group["nationality"] and row.nationality:
            group["nationality"] = row.nationality
    results.extend(
        GlobalSearchResult(
            "Jugador",
            int(group["id"]),
            str(group["name"]),
            group["nationality"],
            ", ".join(
                sorted(
                    f"{team_name} · {league_name}"
                    for _, team_name, league_name, _, _ in group["teams"]
                )
            ),
            None,
            tuple(sorted(group["teams"], key=lambda link: (link[4], link[1].casefold()))),
        )
        for group in groups
    )
    return results
