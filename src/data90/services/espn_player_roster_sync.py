"""Enrich current player profiles from ESPN rosters for selected competitions."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Player,
    PlayerTeamSeason,
    Season,
    SourcePlayer,
    SyncRun,
    SyncRunItem,
    Team,
    TeamAlias,
)
from data90.domain.enums import SyncMode, SyncStatus
from data90.providers.espn import ESPN_COMPETITION_SLUGS, ESPNProvider
from data90.repositories.players import team_squad_for_team
from data90.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]
SOURCE_CODE = ESPNProvider.source_code
TARGET_CODES = (
    "BUNDESLIGA",
    "LIGUE_1",
    "EREDIVISIE",
    "UCL",
    "UEL",
)
TEAM_SUFFIXES = {"fc", "afc", "cf", "sc", "ac", "club", "the"}
ESPN_TEAM_NAME_ALIASES = {
    "BUNDESLIGA": {
        "FC Cologne": "1. FC Köln",
        "Hamburg SV": "Hamburger SV",
        "SV Elversberg": "SV 07 Elversberg",
        "TSG Hoffenheim": "TSG 1899 Hoffenheim",
        "Werder Bremen": "SV Werder Bremen",
    },
    "LIGUE_1": {
        "Lens": "Racing Club de Lens",
        "Lyon": "Olympique Lyonnais",
    },
}


@dataclass(frozen=True, slots=True)
class ESPNPlayerRosterSummary:
    run_id: UUID | None
    status: SyncStatus
    requests_count: int
    teams_count: int
    players_matched: int
    players_inserted: int
    identities_inserted: int
    memberships_inserted: int
    nationality_filled: int
    position_filled: int
    birth_dates_filled: int
    skipped_count: int
    error_count: int
    message: str


def _source(session: Session, provider: ESPNProvider) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
    if source is None:
        source = DataSource(
            code=SOURCE_CODE,
            name="ESPN Soccer",
            base_url=provider.base_url,
            priority=6,
            enabled=True,
            requires_api_key=False,
            license_name="Feed público no documentado; sujeto a términos de ESPN",
            attribution_text="Datos de planteles consultados en ESPN",
            terms_url="https://www.espn.com/adoperations/marketingspecs/",
        )
        session.add(source)
        session.flush()
    return source


def _birth_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _team_key(value: str) -> str:
    words = normalize_name(value).split()
    while words and words[-1] in TEAM_SUFFIXES:
        words.pop()
    return " ".join(words)


def _team_name_index(session: Session, team_ids: set[int]) -> dict[str, set[int]]:
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


def _roster_team_ids(
    *, code: str, rows: list[dict[str, Any]], names_by_team: dict[str, set[int]]
) -> tuple[dict[str, int], int]:
    result: dict[str, int] = {}
    skipped = 0
    aliases = ESPN_TEAM_NAME_ALIASES.get(code, {})
    for row in rows:
        team = row.get("team")
        if not isinstance(team, dict) or not team.get("id"):
            skipped += 1
            continue
        display_name = team.get("displayName") or team.get("name")
        candidate_names = {
            aliases.get(str(value), str(value))
            for value in (
                display_name,
                team.get("name"),
                team.get("shortDisplayName"),
            )
            if value
        }
        team_ids = {
            team_id
            for value in candidate_names
            for team_id in names_by_team.get(_team_key(value), set())
        }
        if len(team_ids) != 1:
            skipped += 1
            continue
        result[str(team["id"])] = next(iter(team_ids))
    return result, skipped


def sync_espn_player_rosters(
    session_factory: SessionFactory,
    provider: ESPNProvider | None = None,
    *,
    competition_codes: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> ESPNPlayerRosterSummary:
    """Sync verified club rosters and player profiles from ESPN."""
    provider = provider or ESPNProvider()
    now = now or datetime.now(UTC)
    selected = tuple(
        code for code in TARGET_CODES if competition_codes is None or code in competition_codes
    )
    if not selected:
        return ESPNPlayerRosterSummary(
            run_id=None,
            status=SyncStatus.SUCCESS,
            requests_count=0,
            teams_count=0,
            players_matched=0,
            players_inserted=0,
            identities_inserted=0,
            memberships_inserted=0,
            nationality_filled=0,
            position_filled=0,
            birth_dates_filled=0,
            skipped_count=0,
            error_count=0,
            message="Sin ligas ESPN seleccionadas",
        )

    with session_factory.begin() as session:
        source = _source(session, provider)
        source_id = source.id
        run = SyncRun(
            mode=SyncMode.DAILY.value,
            status=SyncStatus.RUNNING.value,
            started_at=now,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    teams_total = matched_total = identities_total = skipped_total = 0
    players_inserted = 0
    memberships_total = 0
    nationality_total = position_total = birth_date_total = error_total = 0
    notes: list[str] = []
    for code in selected:
        requests_before = provider.request_count
        inserted_before = identities_total
        fields_before = (nationality_total, position_total, birth_date_total)
        skipped = errors = 0
        status = SyncStatus.SUCCESS.value
        competition_id = None
        message = None
        try:
            slug = ESPN_COMPETITION_SLUGS[code]
            with session_factory() as session:
                competition = session.scalar(select(Competition).where(Competition.code == code))
                competition_id = competition.id if competition else None
                competition_season = (
                    session.scalar(
                        select(CompetitionSeason)
                        .join(Season, Season.id == CompetitionSeason.season_id)
                        .where(
                            CompetitionSeason.competition_id == competition_id,
                            Season.active.is_(True),
                        )
                        .order_by(Season.start_year.desc())
                        .limit(1)
                    )
                    if competition_id is not None
                    else None
                )
                if competition_season is None:
                    raise ValueError(f"No hay temporada activa para {code}")
                competition_season_id = competition_season.id
                team_ids = set(
                    session.scalars(
                        select(Match.home_team_id)
                        .where(Match.competition_season_id == competition_season_id)
                        .union(
                            select(Match.away_team_id).where(
                                Match.competition_season_id == competition_season_id
                            )
                        )
                    ).all()
                )
                names_by_team = _team_name_index(session, team_ids)
                players_by_team = {
                    team_id: {
                        _team_key(member.player_name): member
                        for member in team_squad_for_team(session, team_id)
                    }
                    for team_id in team_ids
                }

            team_response = provider.teams(slug)
            team_list = (
                team_response.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
            )
            if not isinstance(team_list, list):
                raise ValueError("ESPN devolvió un listado de equipos inesperado")
            team_map, unresolved_teams = _roster_team_ids(
                code=code,
                rows=team_list,
                names_by_team=names_by_team,
            )
            skipped += unresolved_teams
            teams_total += len(team_map)

            for external_team_id, team_id in team_map.items():
                try:
                    roster = provider.team_roster(slug, external_team_id)
                    athletes = roster.get("athletes", [])
                    if not isinstance(athletes, list):
                        skipped += 1
                        continue
                    for athlete in athletes:
                        if not isinstance(athlete, dict):
                            skipped += 1
                            continue
                        external_id = athlete.get("id")
                        name = athlete.get("fullName") or athlete.get("displayName")
                        if external_id is None or not isinstance(name, str) or not name.strip():
                            skipped += 1
                            continue
                        member = players_by_team.get(team_id, {}).get(_team_key(name))
                        nationality = athlete.get("citizenship")
                        position_info = athlete.get("position")
                        position = (
                            position_info.get("displayName")
                            if isinstance(position_info, dict)
                            else None
                        )
                        birth_date = _birth_date(athlete.get("dateOfBirth"))
                        with session_factory.begin() as session:
                            identity = session.scalar(
                                select(SourcePlayer).where(
                                    SourcePlayer.source_id == source_id,
                                    SourcePlayer.external_id == str(external_id),
                                )
                            )
                            player_id = identity.player_id if identity is not None else None
                            if (
                                identity is not None
                                and member is not None
                                and identity.player_id != member.player_id
                            ):
                                player = session.get(Player, player_id)
                                if player is None or _team_key(player.display_name) != _team_key(
                                    name
                                ):
                                    skipped += 1
                                    continue
                            if identity is not None and member is None:
                                player = session.get(Player, player_id)
                                if player is None or _team_key(player.display_name) != _team_key(
                                    name
                                ):
                                    skipped += 1
                                    continue
                                conflicting_membership = session.scalar(
                                    select(PlayerTeamSeason.id)
                                    .where(
                                        PlayerTeamSeason.player_id == player_id,
                                        PlayerTeamSeason.competition_season_id
                                        == competition_season_id,
                                        PlayerTeamSeason.team_id != team_id,
                                        PlayerTeamSeason.active.is_(True),
                                    )
                                    .limit(1)
                                )
                                if conflicting_membership is not None:
                                    skipped += 1
                                    continue
                            if identity is None:
                                if member is not None:
                                    player_id = member.player_id
                                else:
                                    player = Player(display_name=name.strip())
                                    session.add(player)
                                    session.flush()
                                    player_id = player.id
                                    players_inserted += 1
                                    if isinstance(nationality, str) and nationality.strip():
                                        player.nationality = nationality.strip()
                                        nationality_total += 1
                                    if isinstance(position, str) and position.strip():
                                        player.position = position.strip()
                                        position_total += 1
                                    if birth_date is not None:
                                        player.birth_date = birth_date
                                        birth_date_total += 1
                                    player.profile_source_id = source_id
                                    player.profile_updated_at = now
                            if identity is None:
                                session.add(
                                    SourcePlayer(
                                        source_id=source_id,
                                        external_id=str(external_id),
                                        player_id=player_id,
                                    )
                                )
                                identities_total += 1
                            elif identity.player_id != player_id:
                                skipped += 1
                                continue
                            player = session.get(Player, player_id)
                            changed = False
                            if (
                                player.nationality is None
                                and isinstance(nationality, str)
                                and nationality.strip()
                            ):
                                player.nationality = nationality.strip()
                                nationality_total += 1
                                changed = True
                            if (
                                player.position is None
                                and isinstance(position, str)
                                and position.strip()
                            ):
                                player.position = position.strip()
                                position_total += 1
                                changed = True
                            if player.birth_date is None and birth_date is not None:
                                player.birth_date = birth_date
                                birth_date_total += 1
                                changed = True
                            if changed:
                                player.profile_source_id = source_id
                                player.profile_updated_at = now
                            membership = session.scalar(
                                select(PlayerTeamSeason).where(
                                    PlayerTeamSeason.player_id == player_id,
                                    PlayerTeamSeason.team_id == team_id,
                                    PlayerTeamSeason.competition_season_id == competition_season_id,
                                    PlayerTeamSeason.source_id == source_id,
                                )
                            )
                            if membership is None:
                                membership = PlayerTeamSeason(
                                    player_id=player_id,
                                    team_id=team_id,
                                    competition_season_id=competition_season_id,
                                    source_id=source_id,
                                )
                                memberships_total += 1
                            membership.position = membership.position or (
                                position.strip() if isinstance(position, str) else None
                            )
                            membership.active = True
                            membership.source_updated_at = now
                            session.add(membership)
                            matched_total += 1
                except Exception as error:
                    errors += 1
                    notes.append(f"{code} {external_team_id}: {error.__class__.__name__}")
            if unresolved_teams:
                notes.append(f"{code}: {unresolved_teams} clubes sin equivalencia unívoca")
        except Exception as error:
            errors += 1
            message = f"{error.__class__.__name__}: {str(error)[:500]}"
        requests = provider.request_count - requests_before
        inserted = identities_total - inserted_before
        updated = sum((nationality_total, position_total, birth_date_total)) - sum(fields_before)
        skipped_total += skipped
        error_total += errors
        if errors:
            status = SyncStatus.PARTIAL.value
        with session_factory.begin() as session:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="ESPN_CURRENT_PLAYER_ROSTER",
                    status=status,
                    requests_count=requests,
                    inserted_count=inserted,
                    updated_count=updated,
                    skipped_count=skipped,
                    error_count=errors,
                    error_code="ESPN_PLAYER_ROSTER_ERROR" if errors else None,
                    message=message or "; ".join(notes[-3:]) or None,
                )
            )

    final_status = SyncStatus.PARTIAL if error_total else SyncStatus.SUCCESS
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        run.status = final_status.value
        run.finished_at = datetime.now(UTC)
        run.requests_count = provider.request_count
        run.inserted_count = identities_total
        run.updated_count = nationality_total + position_total + birth_date_total
        run.skipped_count = skipped_total
        run.error_count = error_total
        run.summary = (
            f"ESPN: {matched_total} perfiles enlazados, {players_inserted} jugadores nuevos y "
            f"{memberships_total} vínculos de plantel; {nationality_total} nacionalidades, "
            f"{position_total} posiciones y {birth_date_total} fechas de nacimiento completadas"
        )
    message = (
        f"ESPN: {teams_total} clubes, {matched_total} perfiles enlazados; "
        f"{memberships_total} vínculos de plantel actualizados; "
        f"{nationality_total} nacionalidades, {position_total} posiciones y "
        f"{birth_date_total} fechas de nacimiento completadas; "
        f"{skipped_total} sin asociación segura, {error_total} errores"
    )
    return ESPNPlayerRosterSummary(
        run_id,
        final_status,
        provider.request_count,
        teams_total,
        matched_total,
        players_inserted,
        identities_total,
        memberships_total,
        nationality_total,
        position_total,
        birth_date_total,
        skipped_total,
        error_total,
        message,
    )
