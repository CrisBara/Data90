"""Incrementally import Highlightly match box scores under a local daily quota."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from data90.database.models import (
    AppSetting,
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
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
from data90.domain.enums import SyncStatus
from data90.providers.highlightly import HighlightlyProvider
from data90.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]
SOURCE_CODE = "highlightly"
DAILY_REQUEST_BUDGET = 80  # Leave headroom within the public plan's 100 requests/day.
PLAYER_PROFILE_DAILY_LIMIT = 40
QUOTA_SETTING = "highlightly_daily_request_usage"
CURSOR_PREFIX = "highlightly_match_listing_cursor_"
LEAGUE_NAMES = {
    "PL": "Premier League",
    "LALIGA": "La Liga",
    "SERIE_A": "Serie A",
    "BUNDESLIGA": "Bundesliga",
    "LIGUE_1": "Ligue 1",
    "EREDIVISIE": "Eredivisie",
    "UCL": "UEFA Champions League",
    "UEL": "UEFA Europa League",
}
LEAGUE_COUNTRIES = {
    "PL": "England",
    "LALIGA": "Spain",
    "SERIE_A": "Italy",
    "BUNDESLIGA": "Germany",
    "LIGUE_1": "France",
    "EREDIVISIE": "Netherlands",
}
TEAM_SUFFIXES = {"fc", "afc", "cf", "sc", "ac", "club", "the"}


class DailyQuotaReached(Exception):
    """No more Highlightly requests are allowed today."""


@dataclass(frozen=True, slots=True)
class HighlightlySyncSummary:
    run_id: UUID | None
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _reserve_daily_request(session_factory: SessionFactory) -> None:
    today = datetime.now(UTC).date().isoformat()
    with session_factory.begin() as session:
        setting = session.scalar(
            select(AppSetting).where(AppSetting.key == QUOTA_SETTING).with_for_update()
        )
        value = setting.value if setting is not None else {}
        used = int(value.get("used", 0)) if value.get("date") == today else 0
        if used >= DAILY_REQUEST_BUDGET:
            raise DailyQuotaReached
        if setting is None:
            setting = AppSetting(key=QUOTA_SETTING, value={})
        setting.value = {"date": today, "used": used + 1}
        session.add(setting)


def _team_key(value: str) -> str:
    words = normalize_name(value).split()
    while words and words[-1] in TEAM_SUFFIXES:
        words.pop()
    return " ".join(words)


def _team_index(session: Session) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for team in session.scalars(select(Team)).all():
        for value in (team.canonical_name, team.short_name):
            if value and _team_key(value):
                result[_team_key(value)].add(team.id)
    for alias, team_id in session.execute(select(TeamAlias.alias, TeamAlias.team_id)):
        if alias and _team_key(alias):
            result[_team_key(alias)].add(team_id)
    return result


def _num(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str):
        try:
            return max(int(value), 0)
        except ValueError:
            return None
    return None


def _player_stats(player_row: dict[str, Any]) -> dict[str, Any]:
    stats = player_row.get("statistics")
    if isinstance(stats, list):
        stats = stats[0] if stats and isinstance(stats[0], dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    values = {
        "minutes_played": _num(player_row.get("minutesPlayed")),
        "is_substitute": player_row.get("isSubstitute")
        if isinstance(player_row.get("isSubstitute"), bool)
        else None,
        "shirt_number": _num(player_row.get("shirtNumber")),
        "position": player_row.get("position")
        if isinstance(player_row.get("position"), str)
        else None,
        "goals": _num(stats.get("goalsScored")),
        "assists": _num(stats.get("assists")),
        "yellow_cards": _num(stats.get("cardsYellow")),
        "red_cards": _num(stats.get("cardsRed")),
        "shots_total": _num(stats.get("shotsTotal")),
        "shots_on_target": _num(stats.get("shotsOnTarget")),
        "goalkeeper_saves": _num(stats.get("saves")),
    }
    return values


def _parse_profile_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for date_format in ("%b %d, %Y", "%d %b %Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _apply_player_profile(player: Player, payload: dict[str, Any], source_id: int) -> bool:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    position = profile.get("position") if isinstance(profile.get("position"), dict) else {}
    values = {
        "nationality": profile.get("citizenship"),
        "birth_date": _parse_profile_date(profile.get("birthDate")),
        "birth_place": profile.get("birthPlace"),
        "height": profile.get("height"),
        "position": position.get("main"),
        "preferred_foot": profile.get("foot"),
        "photo_url": payload.get("logo"),
    }
    changed = False
    for field, value in values.items():
        if isinstance(value, str):
            value = value.strip() or None
        if value is not None and getattr(player, field) is None:
            setattr(player, field, value)
            changed = True
    if changed and player.profile_source_id is None:
        player.profile_source_id = source_id
    player.profile_updated_at = datetime.now(UTC)
    return changed


def _match_candidates(
    session: Session,
    *,
    competition_season_id: int,
    home_team_id: int,
    away_team_id: int,
    match_date: date,
) -> list[Match]:
    candidates = session.scalars(
        select(Match).where(
            Match.competition_season_id == competition_season_id,
            Match.home_team_id == home_team_id,
            Match.away_team_id == away_team_id,
        )
    ).all()
    return [match for match in candidates if abs((match.kickoff_utc.date() - match_date).days) <= 1]


def _get_player(
    session: Session, *, source_id: int, external_id: object, name: str
) -> Player | None:
    source_player = session.scalar(
        select(SourcePlayer).where(
            SourcePlayer.source_id == source_id,
            SourcePlayer.external_id == str(external_id),
        )
    )
    if source_player:
        return session.get(Player, source_player.player_id)
    key = normalize_name(name)
    candidates = [
        player
        for player in session.scalars(select(Player)).all()
        if normalize_name(player.display_name) == key
    ]
    if len(candidates) > 1:
        return None
    player = candidates[0] if candidates else Player(display_name=name.strip())
    if not candidates:
        session.add(player)
        session.flush()
    session.add(
        SourcePlayer(source_id=source_id, external_id=str(external_id), player_id=player.id)
    )
    return player


def _rebuild_season_totals(
    session: Session,
    *,
    source_id: int,
    competition_season_id: int,
    player_ids: set[int],
) -> None:
    for player_id in player_ids:
        rows = session.scalars(
            select(PlayerMatchStatistic).where(
                PlayerMatchStatistic.source_id == source_id,
                PlayerMatchStatistic.player_id == player_id,
                PlayerMatchStatistic.match_id.in_(
                    select(Match.id).where(Match.competition_season_id == competition_season_id)
                ),
            )
        ).all()
        played = [row for row in rows if (row.minutes_played or 0) > 0]
        if not rows:
            continue
        totals = {
            "appearances": len(played),
            "starts": sum(row.is_substitute is False for row in played),
            "substitute_appearances": sum(row.is_substitute is True for row in played),
            "minutes_played": sum(row.minutes_played or 0 for row in played),
        }
        for field in (
            "goals",
            "assists",
            "yellow_cards",
            "red_cards",
            "shots_total",
            "shots_on_target",
        ):
            values = [getattr(row, field) for row in played]
            totals[field] = (
                sum(values) if values and all(value is not None for value in values) else None
            )
        keeper_rows = [
            row
            for row in played
            if (row.position or "").casefold() in {"g", "gk", "goalkeeper", "keeper"}
        ]
        totals["goals_conceded"] = (
            sum(row.goals_conceded or 0 for row in keeper_rows)
            if keeper_rows and all(row.goals_conceded is not None for row in keeper_rows)
            else None
        )
        totals["clean_sheets"] = (
            sum(row.clean_sheet is True for row in keeper_rows)
            if keeper_rows and all(row.clean_sheet is not None for row in keeper_rows)
            else None
        )
        statistic = session.scalar(
            select(PlayerCompetitionSeasonStatistic).where(
                PlayerCompetitionSeasonStatistic.source_id == source_id,
                PlayerCompetitionSeasonStatistic.player_id == player_id,
                PlayerCompetitionSeasonStatistic.competition_season_id == competition_season_id,
            )
        )
        if statistic is None:
            statistic = PlayerCompetitionSeasonStatistic(
                source_id=source_id,
                player_id=player_id,
                competition_season_id=competition_season_id,
            )
            session.add(statistic)
        for field, value in totals.items():
            setattr(statistic, field, value)
        statistic.source_updated_at = datetime.now(UTC)


def sync_highlightly_match_statistics(
    session_factory: SessionFactory,
    *,
    provider: HighlightlyProvider | None = None,
    competition_codes: tuple[str, ...] | None = None,
) -> HighlightlySyncSummary:
    provider = provider or HighlightlyProvider()
    provider.before_request = lambda: _reserve_daily_request(session_factory)
    inserted = updated = skipped = errors = 0
    profiles_updated = 0
    quota_reached = False
    notes: list[str] = []
    touched: dict[int, set[int]] = defaultdict(set)
    with session_factory() as session:
        source = session.scalar(select(DataSource).where(DataSource.code == SOURCE_CODE))
        if source is None:
            notes.append("fuente Highlightly aún no registrada; ejecutar bootstrap")
            return HighlightlySyncSummary(None, SyncStatus.FAILED, 0, 0, 0, 0, 1, "; ".join(notes))
        aliases = _team_index(session)
        seasons = session.execute(
            select(Competition, CompetitionSeason, Season)
            .join(CompetitionSeason, CompetitionSeason.competition_id == Competition.id)
            .join(Season, Season.id == CompetitionSeason.season_id)
            .where(Competition.active.is_(True), Season.active.is_(True))
            .order_by(Competition.display_order, Competition.code)
        ).all()
        if competition_codes:
            seasons = [row for row in seasons if row[0].code in set(competition_codes)]
        for competition, competition_season, season in seasons:
            league_name = LEAGUE_NAMES.get(competition.code)
            if not league_name:
                continue
            try:
                cursor_key = f"{CURSOR_PREFIX}{competition_season.id}"
                cursor = session.get(AppSetting, cursor_key)
                offset = int(cursor.value.get("offset", 0)) if cursor else 0
                listing = provider.get_matches(
                    league_name=league_name,
                    country_name=LEAGUE_COUNTRIES.get(competition.code),
                    season=season.start_year,
                    limit=100,
                    offset=offset,
                )
                matches = listing.get("data", [])
                pagination = listing.get("pagination")
                pagination_offset = (
                    _num(pagination.get("offset")) if isinstance(pagination, dict) else None
                )
                total_count = (
                    _num(pagination.get("totalCount")) if isinstance(pagination, dict) else None
                )
                for item in matches:
                    if not isinstance(item, dict):
                        skipped += 1
                        continue
                    state = item.get("state") if isinstance(item.get("state"), dict) else {}
                    if "finished" not in str(state.get("description", "")).casefold():
                        continue
                    home = item.get("homeTeam") if isinstance(item.get("homeTeam"), dict) else {}
                    away = item.get("awayTeam") if isinstance(item.get("awayTeam"), dict) else {}
                    home_ids = aliases.get(_team_key(str(home.get("name") or "")), set())
                    away_ids = aliases.get(_team_key(str(away.get("name") or "")), set())
                    kickoff = item.get("date") or item.get("startDate")
                    external_id = item.get("id")
                    if len(home_ids) != 1 or len(away_ids) != 1 or not kickoff or not external_id:
                        skipped += 1
                        continue
                    try:
                        match_day = datetime.fromisoformat(
                            str(kickoff).replace("Z", "+00:00")
                        ).date()
                    except ValueError:
                        skipped += 1
                        continue
                    candidates = _match_candidates(
                        session,
                        competition_season_id=competition_season.id,
                        home_team_id=next(iter(home_ids)),
                        away_team_id=next(iter(away_ids)),
                        match_date=match_day,
                    )
                    if len(candidates) != 1:
                        skipped += 1
                        continue
                    local_match = candidates[0]
                    mapping = session.scalar(
                        select(SourceMatch).where(
                            SourceMatch.source_id == source.id,
                            SourceMatch.external_id == str(external_id),
                        )
                    )
                    if mapping is None:
                        mapping = SourceMatch(
                            source_id=source.id,
                            external_id=str(external_id),
                            match_id=local_match.id,
                        )
                        session.add(mapping)
                        session.flush()
                    elif mapping.match_id != local_match.id:
                        skipped += 1
                        continue
                    if mapping.raw_hash:
                        continue
                    box_score = provider.get_box_score(int(external_id))
                    digest = hashlib.sha256(
                        json.dumps(box_score, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()
                    scores = state.get("score") if isinstance(state.get("score"), dict) else {}
                    score = scores.get("current")
                    home_goals = away_goals = None
                    if isinstance(score, dict):
                        home_goals = _num(score.get("home"))
                        away_goals = _num(score.get("away"))
                    elif isinstance(score, str) and "-" in score:
                        left, right = score.split("-", 1)
                        home_goals, away_goals = _num(left.strip()), _num(right.strip())
                    match_player_ids: set[int] = set()
                    team_entries = 0
                    for team_row in box_score:
                        api_team = (
                            team_row.get("team") if isinstance(team_row.get("team"), dict) else {}
                        )
                        team_ids = aliases.get(_team_key(str(api_team.get("name") or "")), set())
                        if len(team_ids) != 1 or next(iter(team_ids)) not in {
                            local_match.home_team_id,
                            local_match.away_team_id,
                        }:
                            continue
                        team_id = next(iter(team_ids))
                        opponent_goals = (
                            away_goals if team_id == local_match.home_team_id else home_goals
                        )
                        player_rows = team_row.get("players") or []
                        for player_row in player_rows:
                            if not isinstance(player_row, dict):
                                continue
                            external_player_id = player_row.get("id")
                            player_name = str(
                                player_row.get("fullName") or player_row.get("name") or ""
                            ).strip()
                            if external_player_id is None or not player_name:
                                skipped += 1
                                continue
                            player = _get_player(
                                session,
                                source_id=source.id,
                                external_id=external_player_id,
                                name=player_name,
                            )
                            if player is None:
                                skipped += 1
                                continue
                            values = _player_stats(player_row)
                            position = values.get("position") or player.position
                            is_keeper = str(position or "").casefold() in {
                                "g",
                                "gk",
                                "goalkeeper",
                                "keeper",
                            }
                            played = (values.get("minutes_played") or 0) > 0
                            statistic = session.scalar(
                                select(PlayerMatchStatistic).where(
                                    PlayerMatchStatistic.match_id == local_match.id,
                                    PlayerMatchStatistic.player_id == player.id,
                                    PlayerMatchStatistic.source_id == source.id,
                                )
                            )
                            if statistic is None:
                                statistic = PlayerMatchStatistic(
                                    match_id=local_match.id,
                                    player_id=player.id,
                                    source_id=source.id,
                                    team_id=team_id,
                                )
                                session.add(statistic)
                                inserted += 1
                            else:
                                updated += 1
                            for key, value in values.items():
                                setattr(statistic, key, value)
                            statistic.team_id = team_id
                            statistic.position = position
                            statistic.goals_conceded = (
                                opponent_goals if is_keeper and played else None
                            )
                            statistic.clean_sheet = (
                                opponent_goals == 0
                                if is_keeper and played and opponent_goals is not None
                                else None
                            )
                            statistic.source_updated_at = datetime.now(UTC)
                            match_player_ids.add(player.id)
                            if played:
                                touched[competition_season.id].add(player.id)
                                membership = session.scalar(
                                    select(PlayerTeamSeason).where(
                                        PlayerTeamSeason.player_id == player.id,
                                        PlayerTeamSeason.team_id == team_id,
                                        PlayerTeamSeason.competition_season_id
                                        == competition_season.id,
                                        PlayerTeamSeason.source_id == source.id,
                                    )
                                )
                                if membership is None:
                                    session.add(
                                        PlayerTeamSeason(
                                            player_id=player.id,
                                            team_id=team_id,
                                            competition_season_id=competition_season.id,
                                            source_id=source.id,
                                            shirt_number=values.get("shirt_number"),
                                            position=position,
                                            active=True,
                                        )
                                    )
                                else:
                                    membership.shirt_number = (
                                        values.get("shirt_number") or membership.shirt_number
                                    )
                                    membership.position = position or membership.position
                            team_entries += 1
                    if team_entries:
                        mapping.raw_hash = digest
                        mapping.source_updated_at = datetime.now(UTC)
                        inserted += 0
                    else:
                        skipped += 1
                    session.commit()
                if cursor is None:
                    cursor = AppSetting(key=cursor_key, value={})
                page_offset = pagination_offset if pagination_offset is not None else offset
                next_offset = page_offset + len(matches)
                if total_count is not None:
                    if next_offset >= total_count:
                        next_offset = 0
                    elif next_offset <= offset:
                        next_offset = min(offset + 100, total_count)
                elif not matches or len(matches) < 100:
                    next_offset = 0
                cursor.value = {"offset": next_offset, "updated_at": datetime.now(UTC).isoformat()}
                session.add(cursor)
                session.commit()
            except DailyQuotaReached:
                session.commit()
                notes.append("límite diario local alcanzado; se continuará en la próxima ejecución")
                quota_reached = True
                break
            except (
                Exception
            ) as exc:  # continue with remaining leagues after individual provider failures
                session.rollback()
                errors += 1
                detail = str(exc)
                api_key = getattr(provider, "api_key", "")
                if api_key:
                    detail = detail.replace(api_key, "[clave oculta]")
                notes.append(f"{competition.code}: {detail[:160] or exc.__class__.__name__}")
        if not quota_reached:
            profile_cutoff = datetime.now(UTC) - timedelta(days=90)
            missing_profile = or_(
                Player.nationality.is_(None),
                Player.birth_date.is_(None),
                Player.preferred_foot.is_(None),
                Player.position.is_(None),
            )
            profile_candidates = session.execute(
                select(Player, SourcePlayer.external_id)
                .join(SourcePlayer, SourcePlayer.player_id == Player.id)
                .where(
                    SourcePlayer.source_id == source.id,
                    missing_profile,
                    or_(
                        Player.profile_updated_at.is_(None),
                        Player.profile_updated_at < profile_cutoff,
                    ),
                )
                .order_by(Player.profile_updated_at.asc().nullsfirst(), Player.id)
                .limit(PLAYER_PROFILE_DAILY_LIMIT)
            ).all()
            for player, external_id in profile_candidates:
                try:
                    profile = provider.get_player_profile(int(external_id))
                    profiles_updated += _apply_player_profile(player, profile, source.id)
                    session.commit()
                except DailyQuotaReached:
                    session.commit()
                    notes.append(
                        "límite diario local alcanzado; los perfiles seguirán "
                        "en la próxima ejecución"
                    )
                    quota_reached = True
                    break
                except Exception as exc:  # one unavailable profile must not block other players
                    session.rollback()
                    errors += 1
                    notes.append(f"perfil {external_id}: {exc.__class__.__name__}")
        for competition_season_id, player_ids in touched.items():
            _rebuild_season_totals(
                session,
                source_id=source.id,
                competition_season_id=competition_season_id,
                player_ids=player_ids,
            )
        session.commit()
    status = (
        SyncStatus.FAILED
        if errors and not inserted and not updated
        else (SyncStatus.PARTIAL if errors else SyncStatus.SUCCESS)
    )
    details = "; ".join(notes[:8])
    message = (
        f"Highlightly: {inserted} filas nuevas, {updated} actualizadas, "
        f"{skipped} sin correspondencia segura, {profiles_updated} perfiles enriquecidos, "
        f"{errors} errores"
    )
    if details:
        message += f". {details}"
    return HighlightlySyncSummary(
        None, status, provider.request_count, inserted, updated, skipped, errors, message
    )
