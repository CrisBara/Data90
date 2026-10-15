"""Import current player profiles, competition statistics, and team memberships."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.database.models import (
    AppSetting,
    Competition,
    DataSource,
    Player,
    PlayerTeamSeason,
    SourceTeam,
    SyncRun,
    SyncRunItem,
    Team,
    TeamAlias,
)
from data90.domain.dto import NormalizedPlayerSeasonStatistic, NormalizedSeason
from data90.domain.enums import SyncMode, SyncStatus
from data90.domain.exceptions import ProviderAuthenticationError, ProviderError
from data90.providers.api_football import ApiFootballProvider
from data90.repositories.catalog import upsert_competition_season
from data90.repositories.players import upsert_player_statistic
from data90.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]
DAILY_REQUEST_BUDGET = 90
CURSOR_SETTING = "api_football_player_import_cursor"
QUOTA_SETTING = "api_football_daily_request_usage"

# API-Football league identifiers for the eight competitions in the local catalog.
LEAGUE_IDS = {
    "PL": 39,
    "LALIGA": 140,
    "SERIE_A": 135,
    "BUNDESLIGA": 78,
    "LIGUE_1": 61,
    "EREDIVISIE": 88,
    "UCL": 2,
    "UEL": 3,
}


class DailyQuotaReached(Exception):
    """The local daily request budget has been consumed."""


def _selected_league_indices(
    competition_codes: tuple[str, ...] | None, next_competition: int
) -> tuple[list[int], int]:
    indices = [
        index
        for index, code in enumerate(LEAGUE_IDS)
        if competition_codes is None or code in competition_codes
    ]
    start_index = next(
        (index for index, global_index in enumerate(indices) if global_index >= next_competition),
        0,
    )
    return indices, start_index


@dataclass(frozen=True, slots=True)
class ApiFootballSyncSummary:
    run_id: UUID | None
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _reserve_daily_request(session_factory: SessionFactory, today: date) -> None:
    with session_factory.begin() as session:
        setting = session.scalar(
            select(AppSetting).where(AppSetting.key == QUOTA_SETTING).with_for_update()
        )
        value = setting.value if setting is not None else {}
        used = int(value.get("used", 0)) if value.get("date") == today.isoformat() else 0
        if used >= DAILY_REQUEST_BUDGET:
            raise DailyQuotaReached
        if setting is None:
            setting = AppSetting(key=QUOTA_SETTING, value={})
        setting.value = {"date": today.isoformat(), "used": used + 1}
        session.add(setting)


def _player_statistics(rows: list[dict[str, Any]]) -> dict[str, int | None]:
    def nested(row: dict[str, Any], parent: str, key: str) -> Any:
        value = row.get(parent)
        return value.get(key) if isinstance(value, dict) else None

    fields = {
        "appearances": lambda row: nested(row, "games", "appearences"),
        "starts": lambda row: nested(row, "games", "lineups"),
        "substitute_appearances": lambda row: nested(row, "substitutes", "in"),
        "minutes_played": lambda row: nested(row, "games", "minutes"),
        "goals": lambda row: nested(row, "goals", "total"),
        "assists": lambda row: nested(row, "goals", "assists"),
        "shots_total": lambda row: nested(row, "shots", "total"),
        "shots_on_target": lambda row: nested(row, "shots", "on"),
        "goals_conceded": lambda row: nested(row, "goals", "conceded"),
        "clean_sheets": lambda row: nested(row, "games", "clean_sheets"),
        "tackles_won": lambda row: nested(row, "tackles", "won"),
        "penalties_scored": lambda row: nested(row, "penalty", "scored"),
        "yellow_cards": lambda row: nested(row, "cards", "yellow"),
        "red_cards": lambda row: nested(row, "cards", "red"),
    }
    totals: dict[str, int | None] = {}
    for name, getter in fields.items():
        values = [getter(row) for row in rows]
        numeric = [
            value for value in values if isinstance(value, int) and not isinstance(value, bool)
        ]
        totals[name] = sum(numeric) if numeric else None
    return totals


def _team_index(session: Session) -> dict[str, set[int]]:
    names: dict[str, set[int]] = {}
    for team in session.scalars(select(Team)).all():
        key = normalize_name(team.canonical_name)
        if key:
            names.setdefault(key, set()).add(team.id)
    aliases = session.execute(select(TeamAlias.normalized_alias, TeamAlias.team_id)).all()
    for alias, team_id in aliases:
        if alias:
            names.setdefault(alias, set()).add(team_id)
    return names


def _store_player(
    session: Session,
    *,
    source_id: int,
    competition_season_id: int,
    league_code: str,
    season_year: int,
    record: dict[str, Any],
    team_index: dict[str, set[int]],
) -> tuple[bool, bool]:
    profile = record.get("player")
    statistics_rows = record.get("statistics")
    if not isinstance(profile, dict) or not isinstance(statistics_rows, list):
        return False, False
    external_id = profile.get("id")
    display_name = profile.get("name")
    if external_id is None or not isinstance(display_name, str) or not display_name.strip():
        return False, False

    statistic_rows = [row for row in statistics_rows if isinstance(row, dict)]
    player_stat = NormalizedPlayerSeasonStatistic(
        source_code=ApiFootballProvider.source_code,
        external_id=str(external_id),
        display_name=display_name.strip(),
        competition_code=league_code,
        season_start_year=season_year,
        **_player_statistics(statistic_rows),
    )
    player, inserted, updated = upsert_player_statistic(
        session, player_stat, competition_season_id=competition_season_id
    )
    profile_updated = _apply_profile(session, player, profile, statistic_rows, source_id)
    _upsert_memberships(
        session,
        player_id=player.id,
        source_id=source_id,
        competition_season_id=competition_season_id,
        statistics_rows=statistic_rows,
        team_index=team_index,
    )
    return inserted, updated or profile_updated


def _apply_profile(
    session: Session,
    player: Player,
    profile: dict[str, Any],
    statistics_rows: list[dict[str, Any]],
    source_id: int,
) -> bool:
    birth = profile.get("birth") if isinstance(profile.get("birth"), dict) else {}
    first_statistics = statistics_rows[0] if statistics_rows else {}
    games = first_statistics.get("games") if isinstance(first_statistics, dict) else {}
    games = games if isinstance(games, dict) else {}
    birth_text = birth.get("date")
    try:
        birth_date = (
            date.fromisoformat(birth_text) if isinstance(birth_text, str) and birth_text else None
        )
    except ValueError:
        birth_date = None
    values = {
        "nationality": profile.get("nationality"),
        "birth_date": birth_date,
        "birth_place": birth.get("place"),
        "height": profile.get("height"),
        "weight": profile.get("weight"),
        "position": games.get("position"),
        "photo_url": profile.get("photo"),
        "preferred_foot": _preferred_foot(profile.get("foot")),
    }
    changed = False
    for field, value in values.items():
        if isinstance(value, str):
            value = value.strip() or None
        if value is not None and getattr(player, field) != value:
            setattr(player, field, value)
            changed = True
    if player.profile_source_id != source_id:
        player.profile_source_id = source_id
        changed = True
    if changed or player.profile_updated_at is None:
        player.profile_updated_at = datetime.now(UTC)
        session.add(player)
    return changed


def _preferred_foot(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return {"left": "LEFT", "right": "RIGHT", "both": "BOTH"}.get(normalized)


def _upsert_memberships(
    session: Session,
    *,
    player_id: int,
    source_id: int,
    competition_season_id: int,
    statistics_rows: list[dict[str, Any]],
    team_index: dict[str, set[int]],
) -> None:
    for row in statistics_rows:
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        external_team_id = team.get("id")
        mapped_team_id = (
            session.scalar(
                select(SourceTeam.team_id).where(
                    SourceTeam.source_id == source_id,
                    SourceTeam.external_id == str(external_team_id),
                )
            )
            if external_team_id is not None
            else None
        )
        name = team.get("name")
        team_ids = team_index.get(normalize_name(name), set()) if isinstance(name, str) else set()
        if mapped_team_id is not None:
            team_id = mapped_team_id
        elif len(team_ids) == 1:
            team_id = next(iter(team_ids))
            if external_team_id is not None:
                session.add(
                    SourceTeam(
                        source_id=source_id,
                        team_id=team_id,
                        external_id=str(external_team_id),
                    )
                )
        else:
            continue
        membership = session.scalar(
            select(PlayerTeamSeason).where(
                PlayerTeamSeason.player_id == player_id,
                PlayerTeamSeason.team_id == team_id,
                PlayerTeamSeason.competition_season_id == competition_season_id,
                PlayerTeamSeason.source_id == source_id,
            )
        )
        games = row.get("games") if isinstance(row.get("games"), dict) else {}
        number = games.get("number")
        position = games.get("position")
        if membership is None:
            membership = PlayerTeamSeason(
                player_id=player_id,
                team_id=team_id,
                competition_season_id=competition_season_id,
                source_id=source_id,
            )
        membership.shirt_number = number if isinstance(number, int) else None
        membership.position = position if isinstance(position, str) else None
        membership.active = True
        membership.source_updated_at = datetime.now(UTC)
        session.add(membership)


def sync_api_football_players(
    session_factory: SessionFactory,
    provider: ApiFootballProvider | None = None,
    *,
    competition_codes: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> ApiFootballSyncSummary:
    """Refresh current-season player data under a persisted 90-call daily budget."""
    provider = provider or ApiFootballProvider()
    now = now or datetime.now(UTC)
    if competition_codes is not None and not competition_codes:
        return ApiFootballSyncSummary(
            None, SyncStatus.SUCCESS, 0, 0, 0, 0, 0, "No hay competiciones seleccionadas"
        )
    if not provider.token:
        return ApiFootballSyncSummary(
            None, SyncStatus.SUCCESS, 0, 0, 0, 0, 0, "API-Football omitido: falta la clave"
        )

    season_year = now.year if now.month >= 7 else now.year - 1
    with session_factory.begin() as session:
        run = SyncRun(mode=SyncMode.DAILY.value, status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        run_id = run.id
        source_id = session.scalar(
            select(DataSource.id).where(DataSource.code == ApiFootballProvider.source_code)
        )
        if source_id is None:
            raise ProviderError(
                "Falta API-Football en el catálogo de fuentes; ejecutá el bootstrap"
            )
        source_run_id = run_id
        setting = session.get(AppSetting, CURSOR_SETTING)
        cursor_value = setting.value if setting is not None else {}
        cursors = (
            cursor_value.get("leagues", {}) if cursor_value.get("season") == season_year else {}
        )
        next_competition = int(cursor_value.get("next_competition", 0)) % len(LEAGUE_IDS)

    all_league_items = list(LEAGUE_IDS.items())
    selected_indices, start_index = _selected_league_indices(
        competition_codes, next_competition
    )
    assert selected_indices
    league_items = [all_league_items[index] for index in selected_indices]
    inserted_total = updated_total = skipped_total = errors_total = 0
    processed_by_competition: dict[str, tuple[int, int, int, int, str, str | None]] = {}
    team_index: dict[str, set[int]] | None = None
    deferred = False
    authentication_failed = False
    plan_limited = False

    def reserve() -> None:
        _reserve_daily_request(session_factory, now.date())

    provider.request_budget = reserve
    current_index = start_index
    for _ in range(DAILY_REQUEST_BUDGET):
        if deferred or authentication_failed:
            break
        code, league_id = league_items[current_index]
        cursor = cursors.get(code, {})
        page = int(cursor.get("page", 1))
        requests_before = provider.request_count
        status = SyncStatus.SUCCESS.value
        message = None
        inserted = updated = skipped = 0
        try:
            payload = provider.get_player_page(league_id, season_year, page)
            rows = payload["response"]
            total_pages = max(1, int(payload["paging"].get("total", 1)))
            with session_factory.begin() as session:
                if team_index is None:
                    team_index = _team_index(session)
                competition_season = upsert_competition_season(
                    session,
                    NormalizedSeason(
                        source_code=ApiFootballProvider.source_code,
                        competition_code=code,
                        external_id=str(league_id),
                        external_code=str(league_id),
                        external_season=str(season_year),
                        start_year=season_year,
                        end_year=season_year + 1,
                    ),
                )
                for record in rows:
                    if not isinstance(record, dict):
                        skipped += 1
                        continue
                    was_inserted, was_updated = _store_player(
                        session,
                        source_id=source_id,
                        competition_season_id=competition_season.id,
                        league_code=code,
                        season_year=season_year,
                        record=record,
                        team_index=team_index,
                    )
                    inserted += int(was_inserted)
                    updated += int(was_updated and not was_inserted)
                    skipped += int(not was_inserted and not was_updated)
            cursors[code] = {"page": page + 1 if page < total_pages else 1}
        except DailyQuotaReached:
            deferred = True
            message = "Tope diario local alcanzado; continúa mañana."
            status = "DEFERRED"
        except ProviderAuthenticationError as error:
            authentication_failed = True
            errors_total += 1
            message = str(error)
            status = SyncStatus.FAILED.value
        except Exception as error:
            message = f"{error.__class__.__name__}: {str(error)[:700]}"
            if "free plans do not have access to this season" in str(error).casefold():
                plan_limited = True
                deferred = True
                status = SyncStatus.SUCCESS.value
                message = (
                    "API-Football no incluye la temporada actual en este plan. "
                    "Las estadísticas actuales se obtienen de Promiedos."
                )
            else:
                errors_total += 1
                status = SyncStatus.FAILED.value
                # A rejected season or unsupported endpoint will not recover by moving to
                # another league. Stop here to avoid burning the free plan's daily quota.
                deferred = True

        if inserted or updated or skipped or message:
            previous = processed_by_competition.get(code, (0, 0, 0, 0, status, None))
            processed_by_competition[code] = (
                previous[0] + inserted,
                previous[1] + updated,
                previous[2] + skipped,
                previous[3] + provider.request_count - requests_before,
                status if status == SyncStatus.FAILED.value else previous[4],
                message or previous[5],
            )
        inserted_total += inserted
        updated_total += updated
        skipped_total += skipped
        current_index = (current_index + 1) % len(league_items)
        if deferred:
            break

    # Mark a full daily budget as partial even when the loop ended exactly on
    # its last permitted request. The saved cursor will resume on the next run.
    if provider.request_count >= DAILY_REQUEST_BUDGET:
        deferred = True

    with session_factory.begin() as session:
        cursor_setting = session.get(AppSetting, CURSOR_SETTING)
        if cursor_setting is None:
            cursor_setting = AppSetting(key=CURSOR_SETTING, value={})
        cursor_setting.value = {
            "season": season_year,
            "leagues": cursors,
            "next_competition": selected_indices[current_index],
        }
        session.add(cursor_setting)
        for code, (
            inserted,
            updated,
            skipped,
            requests,
            status,
            message,
        ) in processed_by_competition.items():
            competition_id = session.scalar(select(Competition.id).where(Competition.code == code))
            session.add(
                SyncRunItem(
                    sync_run_id=source_run_id,
                    source_id=source_id,
                    competition_id=competition_id,
                    operation="API_FOOTBALL_CURRENT_PLAYERS",
                    status=status,
                    requests_count=requests,
                    inserted_count=inserted,
                    updated_count=updated,
                    skipped_count=skipped,
                    error_count=int(status == SyncStatus.FAILED.value),
                    error_code="API_FOOTBALL_ERROR" if status == SyncStatus.FAILED.value else None,
                    message=message,
                )
            )
        run = session.get(SyncRun, run_id)
        run.finished_at = datetime.now(UTC)
        run.status = (
            SyncStatus.FAILED.value
            if authentication_failed and inserted_total + updated_total == 0
            else SyncStatus.PARTIAL.value
            if errors_total or (deferred and not plan_limited)
            else SyncStatus.SUCCESS.value
        )
        run.requests_count = provider.request_count
        run.inserted_count = inserted_total
        run.updated_count = updated_total
        run.skipped_count = skipped_total
        run.error_count = errors_total
        run.summary = (
            "API-Football no habilita la temporada actual en el plan configurado; "
            "los datos actuales se sincronizan desde Promiedos"
            if plan_limited
            else "Datos actuales de jugadores API-Football; se reanuda la paginación diariamente"
        )

    return ApiFootballSyncSummary(
        run_id,
        SyncStatus.FAILED
        if authentication_failed and inserted_total + updated_total == 0
        else SyncStatus.PARTIAL
        if errors_total or (deferred and not plan_limited)
        else SyncStatus.SUCCESS,
        provider.request_count,
        inserted_total,
        updated_total,
        skipped_total,
        errors_total,
        (
            "Se omitió la temporada actual de API-Football por restricción del plan"
            if plan_limited
            else "Se alcanzó la cuota diaria de API-Football; la paginación quedó guardada para retomarse en la próxima actualización"
            if deferred
            else "Planteles, perfiles y estadísticas de temporada API-Football sincronizados"
        ),
    )
