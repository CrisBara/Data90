"""Synchronize Promiedos season fixtures and results for the current 2026/27 season."""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from football_analytics.config.constants import COMPETITIONS, SOURCES
from football_analytics.config.settings import get_settings
from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchEvent,
    MatchTeamStatistic,
    Season,
    SourceMatch,
    SourceTeam,
    SyncRun,
    SyncRunItem,
    Team,
    TeamAlias,
)
from football_analytics.domain.dto import NormalizedMatch
from football_analytics.domain.enums import MatchStatus, SyncMode, SyncStatus
from football_analytics.domain.exceptions import ProviderError
from football_analytics.providers.promiedos import PromiedosProvider
from football_analytics.repositories.match_sync import upsert_match
from football_analytics.services.promiedos_sync import PROMIEDOS_LEAGUE_IDS
from football_analytics.sync.normalizer import normalize_name

SessionFactory = Callable[[], Session]
FINISHED_STATUS = 3
LIVE_STATUS = 2
SCHEDULED_STATUS = 1
TEAM_WORD_EQUIVALENTS = {
    "friburgo": "freiburg",
    "hamburgo": "hamburger",
    "munich": "munchen",
    "milan": "mailand",
    "estrasburgo": "strasbourg",
    "niza": "nice",
    "marsella": "marseille",
    "rennes": "rennais",
    "brujas": "brugge",
    "praga": "praha",
    "atenas": "athens",
    "sheva": "scheva",
    "salzburgo": "salzburg",
    "pilsen": "plzen",
    "lyon": "lyonnais",
    "olympiacos": "olympiakos",
}


def _team_key(value: str) -> str:
    compact_acronyms = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", "", value)
    return normalize_name(compact_acronyms)


def _key_variants(key: str) -> list[str]:
    variants = {key}
    for original, replacement in TEAM_WORD_EQUIVALENTS.items():
        variants.update(
            " ".join(replacement if word == original else word for word in key.split())
            for _ in (0,)
        )
    return list(variants)


@dataclass(frozen=True, slots=True)
class PromiedosMatchSyncSummary:
    run_id: str
    status: SyncStatus
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _round_number(name: str | None) -> int | None:
    if not name:
        return None
    match = re.search(r"(?:fecha|jornada|round)\s*(\d+)", name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _round_filters(
    data: dict,
    *,
    competition_code: str,
    full_season: bool,
    relevant_matchdays: set[int] | None = None,
) -> list[str]:
    filters = (data.get("games") or {}).get("filters") or []
    rounds = [
        item
        for item in filters
        if isinstance(item, dict)
        and isinstance(item.get("key"), str)
        and _round_number(item.get("name")) is not None
    ]
    if competition_code == "UCL":
        template = next(
            (
                item["key"].rsplit("_", 1)[0]
                for item in filters
                if isinstance(item, dict)
                and "temporada regular" in str(item.get("name", "")).casefold()
            ),
            None,
        )
        if template:
            rounds = [
                {"name": f"Fecha {round_number}", "key": f"{template}_{round_number}"}
                for round_number in range(1, 9)
            ]
    if full_season:
        return [item["key"] for item in rounds]
    selected = next((i for i, item in enumerate(rounds) if item.get("selected")), None)
    if selected is not None:
        days = relevant_matchdays or set()
        selected_days = {
            _round_number(rounds[index].get("name")) for index in (selected, max(0, selected - 1))
        }
        days.update(number for number in selected_days if number is not None)
        relevant_matchdays = days
    if relevant_matchdays:
        return [
            item["key"] for item in rounds if _round_number(item.get("name")) in relevant_matchdays
        ]
    return []


def _status(game: dict) -> MatchStatus:
    status = game.get("status") or {}
    code = status.get("enum")
    if code == FINISHED_STATUS:
        return MatchStatus.FINISHED
    if code == LIVE_STATUS:
        return MatchStatus.LIVE
    if code == SCHEDULED_STATUS:
        return MatchStatus.SCHEDULED
    label = normalize_name(str(status.get("name") or status.get("short_name") or ""))
    if any(word in label for word in ("final", "finalizado", "finished")):
        return MatchStatus.FINISHED
    if any(word in label for word in ("juego", "curso", "live")):
        return MatchStatus.LIVE
    if any(word in label for word in ("aplaz", "postpon")):
        return MatchStatus.POSTPONED
    if any(word in label for word in ("suspend",)):
        return MatchStatus.SUSPENDED
    if any(word in label for word in ("cancel",)):
        return MatchStatus.CANCELLED
    if any(word in label for word in ("prog", "scheduled")):
        return MatchStatus.SCHEDULED
    return MatchStatus.UNKNOWN


def _score(value: object) -> int | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return int(value) if float(value).is_integer() else None


def _match_dto(game: dict, *, code: str, year: int, timezone: ZoneInfo) -> NormalizedMatch:
    game_id = game.get("id")
    teams = game.get("teams")
    kickoff_text = game.get("start_time")
    if not game_id or not isinstance(teams, list) or len(teams) != 2 or not kickoff_text:
        raise ProviderError("Promiedos devolvió un partido incompleto")
    kickoff = datetime.strptime(kickoff_text, "%d-%m-%Y %H:%M").replace(tzinfo=timezone)
    scores = game.get("scores") if isinstance(game.get("scores"), list) else [None, None]
    if len(scores) < 2:
        scores = [None, None]
    round_label = game.get("stage_round_name")
    matchday = _round_number(round_label)
    return NormalizedMatch(
        source_code=PromiedosProvider.source_code,
        external_id=str(game_id),
        competition_code=code,
        season_start_year=year,
        kickoff_utc=kickoff.astimezone(UTC),
        home_external_id=str(teams[0].get("id") or ""),
        away_external_id=str(teams[1].get("id") or ""),
        home_name=str(teams[0].get("name") or ""),
        away_name=str(teams[1].get("name") or ""),
        status=_status(game),
        matchday=matchday,
        round_label=round_label,
        stage=None,
        home_score_ft=_score(scores[0]),
        away_score_ft=_score(scores[1]),
    )


def _team_resolver(
    session: Session,
    source_id: int,
    primary_source_id: int | None,
    competition_season_id: int,
) -> tuple[dict[str, set[int]], dict[str, int]]:
    statement = select(Match.home_team_id, Match.away_team_id).where(
        Match.competition_season_id == competition_season_id
    )
    if primary_source_id is not None:
        statement = statement.join(SourceMatch, SourceMatch.match_id == Match.id).where(
            SourceMatch.source_id == primary_source_id
        )
    current_ids = {team_id for pair in session.execute(statement) for team_id in pair}
    source_ids = {
        external_id: team_id
        for external_id, team_id in session.execute(
            select(SourceTeam.external_id, SourceTeam.team_id).where(
                SourceTeam.source_id == source_id,
                SourceTeam.team_id.in_(current_ids) if current_ids else False,
            )
        )
    }
    aliases: dict[str, set[int]] = defaultdict(set)
    if current_ids:
        for team in session.scalars(select(Team).where(Team.id.in_(current_ids))):
            for name in (team.canonical_name, team.short_name):
                if name:
                    aliases[_team_key(name)].add(team.id)
        for name, team_id in session.execute(
            select(TeamAlias.alias, TeamAlias.team_id).where(TeamAlias.team_id.in_(current_ids))
        ):
            aliases[_team_key(name)].add(team_id)
    return aliases, source_ids


def _resolve_team(
    external_id: str, name: str, aliases: dict[str, set[int]], source_ids: dict[str, int]
) -> int | None:
    mapped = source_ids.get(external_id)
    if mapped is not None:
        return mapped
    key = _team_key(name)
    variants = _key_variants(key)
    candidates = set().union(*(aliases.get(item, set()) for item in variants))
    if len(candidates) == 1:
        return next(iter(candidates))
    # Providers localize or abbreviate club names; accept a token subset only when
    # it points to exactly one participant in the current competition season.
    candidates = {
        team_id
        for alias, team_ids in aliases.items()
        if alias and any(set(item.split()).issubset(set(alias.split())) for item in variants)
        for team_id in team_ids
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _merge_match_children(
    session: Session,
    old_match: Match,
    new_match: Match,
    old_to_new_team: dict[int, int],
) -> None:
    for statistic in session.scalars(
        select(MatchTeamStatistic).where(MatchTeamStatistic.match_id == old_match.id)
    ):
        new_team_id = old_to_new_team.get(statistic.team_id, statistic.team_id)
        existing = session.scalar(
            select(MatchTeamStatistic).where(
                MatchTeamStatistic.match_id == new_match.id,
                MatchTeamStatistic.team_id == new_team_id,
                MatchTeamStatistic.source_id == statistic.source_id,
            )
        )
        if existing is None:
            statistic.match_id = new_match.id
            statistic.team_id = new_team_id
        else:
            for field in (
                "possession",
                "shots_total",
                "shots_on_target",
                "shots_off_target",
                "shots_blocked",
                "shots_inside_box",
                "shots_outside_box",
                "corners",
                "fouls",
                "offsides",
                "yellow_cards",
                "red_cards",
                "passes_total",
                "passes_accurate",
                "goalkeeper_saves",
                "source_updated_at",
            ):
                if getattr(existing, field) is None:
                    setattr(existing, field, getattr(statistic, field))
            session.delete(statistic)
    for event in session.scalars(select(MatchEvent).where(MatchEvent.match_id == old_match.id)):
        event.match_id = new_match.id
        if event.team_id in old_to_new_team:
            event.team_id = old_to_new_team[event.team_id]
    for source_match in session.scalars(
        select(SourceMatch).where(SourceMatch.match_id == old_match.id)
    ):
        source_match.match_id = new_match.id
    session.flush()
    session.delete(old_match)


def _remap_match_children_in_place(
    session: Session, match: Match, old_to_new_team: dict[int, int]
) -> None:
    for statistic in session.scalars(
        select(MatchTeamStatistic).where(MatchTeamStatistic.match_id == match.id)
    ):
        new_team_id = old_to_new_team.get(statistic.team_id, statistic.team_id)
        if new_team_id == statistic.team_id:
            continue
        existing = session.scalar(
            select(MatchTeamStatistic).where(
                MatchTeamStatistic.match_id == match.id,
                MatchTeamStatistic.team_id == new_team_id,
                MatchTeamStatistic.source_id == statistic.source_id,
            )
        )
        if existing is None:
            statistic.team_id = new_team_id
        else:
            for field in (
                "possession",
                "shots_total",
                "shots_on_target",
                "shots_off_target",
                "shots_blocked",
                "shots_inside_box",
                "shots_outside_box",
                "corners",
                "fouls",
                "offsides",
                "yellow_cards",
                "red_cards",
                "passes_total",
                "passes_accurate",
                "goalkeeper_saves",
                "source_updated_at",
            ):
                if getattr(existing, field) is None:
                    setattr(existing, field, getattr(statistic, field))
            session.delete(statistic)
    for event in session.scalars(select(MatchEvent).where(MatchEvent.match_id == match.id)):
        if event.team_id in old_to_new_team:
            event.team_id = old_to_new_team[event.team_id]


def _align_existing_source_match(
    session: Session,
    *,
    source_id: int,
    competition_season_id: int,
    dto: NormalizedMatch,
    home_team_id: int,
    away_team_id: int,
    timezone: ZoneInfo,
) -> None:
    source_match = session.scalar(
        select(SourceMatch).where(
            SourceMatch.source_id == source_id,
            SourceMatch.external_id == dto.external_id,
        )
    )
    if source_match is None:
        return
    old_match = session.get(Match, source_match.match_id)
    if old_match is None or (
        old_match.competition_season_id == competition_season_id
        and old_match.home_team_id == home_team_id
        and old_match.away_team_id == away_team_id
    ):
        return
    candidates = session.scalars(
        select(Match).where(
            Match.competition_season_id == competition_season_id,
            Match.home_team_id == home_team_id,
            Match.away_team_id == away_team_id,
        )
    ).all()
    local_day = dto.kickoff_utc.astimezone(timezone).date()
    exact_day = [
        match for match in candidates if match.kickoff_utc.astimezone(timezone).date() == local_day
    ]
    round_matches = [
        match for match in candidates if dto.matchday is not None and match.matchday == dto.matchday
    ]
    target = (
        exact_day[0]
        if len(exact_day) == 1
        else (
            round_matches[0]
            if len(round_matches) == 1
            else candidates[0]
            if len(candidates) == 1
            else None
        )
    )
    if target is not None:
        other_sources = session.scalar(
            select(SourceMatch.id)
            .where(
                SourceMatch.match_id == old_match.id,
                SourceMatch.source_id != source_id,
            )
            .limit(1)
        )
        old_to_new = {
            old_match.home_team_id: home_team_id,
            old_match.away_team_id: away_team_id,
        }
        source_match.match_id = target.id
        if other_sources is None:
            _merge_match_children(session, old_match, target, old_to_new)
        return
    other_sources = session.scalar(
        select(SourceMatch.id)
        .where(
            SourceMatch.match_id == old_match.id,
            SourceMatch.source_id != source_id,
        )
        .limit(1)
    )
    if other_sources is not None or old_match.competition_season_id != competition_season_id:
        raise ProviderError("El mapping Promiedos contradice un partido de otra fuente")
    old_to_new = {
        old_match.home_team_id: home_team_id,
        old_match.away_team_id: away_team_id,
    }
    old_match.home_team_id = home_team_id
    old_match.away_team_id = away_team_id
    _remap_match_children_in_place(session, old_match, old_to_new)


def sync_promiedos_match_fixtures(
    session_factory: SessionFactory,
    *,
    provider: PromiedosProvider | None = None,
    competition_codes: tuple[str, ...] | None = None,
    full_season: bool = True,
) -> PromiedosMatchSyncSummary:
    """Import every currently listed round, or only recent/selected rounds for daily sync."""
    provider = provider or PromiedosProvider()
    definitions = [
        (code, league_id)
        for code, league_id in PROMIEDOS_LEAGUE_IDS.items()
        if competition_codes is None or code in competition_codes
    ]
    settings = get_settings()
    timezone = ZoneInfo(settings.app_timezone)
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        source = session.scalar(select(DataSource).where(DataSource.code == provider.source_code))
        if source is None:
            config = next(item for item in SOURCES if item["code"] == provider.source_code)
            source = DataSource(**config)
            session.add(source)
            session.flush()
        run = SyncRun(
            mode=SyncMode.BOOTSTRAP.value if full_season else SyncMode.DAILY.value,
            status=SyncStatus.RUNNING.value,
            started_at=now,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    requests_count = inserted_count = updated_count = skipped_count = error_count = 0
    failures = 0
    for code, league_id in definitions:
        request_start = provider.request_count
        inserted = updated = skipped = 0
        status = SyncStatus.SUCCESS.value
        message = None
        competition_id = None
        try:
            catalog = provider.get_league_data(league_id)
            relevant_matchdays: set[int] = set()
            if not full_season:
                local_today = now.astimezone(timezone).date()
                window_start = datetime.combine(
                    local_today - timedelta(days=7), datetime.min.time(), tzinfo=timezone
                ).astimezone(UTC)
                window_end = datetime.combine(
                    local_today + timedelta(days=14), datetime.max.time(), tzinfo=timezone
                ).astimezone(UTC)
                with session_factory() as session:
                    competition = session.scalar(
                        select(Competition).where(Competition.code == code)
                    )
                    season = session.scalar(select(Season).where(Season.start_year == now.year))
                    if competition and season:
                        competition_season = session.scalar(
                            select(CompetitionSeason).where(
                                CompetitionSeason.competition_id == competition.id,
                                CompetitionSeason.season_id == season.id,
                            )
                        )
                        if competition_season:
                            relevant_matchdays.update(
                                number
                                for (number,) in session.execute(
                                    select(Match.matchday).where(
                                        Match.competition_season_id == competition_season.id,
                                        Match.matchday.is_not(None),
                                        Match.kickoff_utc >= window_start,
                                        Match.kickoff_utc <= window_end,
                                    )
                                )
                                if number is not None
                            )
            filter_keys = _round_filters(
                catalog,
                competition_code=code,
                full_season=full_season,
                relevant_matchdays=relevant_matchdays,
            )
            if not filter_keys:
                raise ProviderError(f"Promiedos no publicó las jornadas actuales para {code}")
            games_by_id: dict[str, dict] = {}
            for filter_key in dict.fromkeys(filter_keys):
                for game in provider.get_league_games(league_id, filter_key):
                    if game.get("id"):
                        games_by_id[str(game["id"])] = game

            with session_factory.begin() as session:
                competition = session.scalar(select(Competition).where(Competition.code == code))
                season = session.scalar(select(Season).where(Season.start_year == now.year))
                if competition is None or season is None:
                    raise ProviderError(f"Falta el catálogo o la temporada 2026/27 para {code}")
                competition_season = session.scalar(
                    select(CompetitionSeason).where(
                        CompetitionSeason.competition_id == competition.id,
                        CompetitionSeason.season_id == season.id,
                    )
                )
                if competition_season is None:
                    raise ProviderError(f"No está inicializada la temporada para {code}")
                competition_id = competition.id
                primary_source_code = next(
                    definition.provider_codes[0][0]
                    for definition in COMPETITIONS
                    if definition.code == code
                )
                primary_source_id = session.scalar(
                    select(DataSource.id).where(DataSource.code == primary_source_code)
                )
                aliases, source_teams = _team_resolver(
                    session, source.id, primary_source_id, competition_season.id
                )
                for game in games_by_id.values():
                    try:
                        dto = _match_dto(game, code=code, year=now.year, timezone=timezone)
                    except (TypeError, ValueError, ProviderError):
                        skipped += 1
                        continue
                    if not full_season:
                        local_date = dto.kickoff_utc.astimezone(timezone).date()
                        today = now.astimezone(timezone).date()
                        if (
                            not today - timedelta(days=7)
                            <= local_date
                            <= today + timedelta(days=14)
                        ):
                            continue
                    home_id = _resolve_team(
                        dto.home_external_id, dto.home_name, aliases, source_teams
                    )
                    away_id = _resolve_team(
                        dto.away_external_id, dto.away_name, aliases, source_teams
                    )
                    if not home_id or not away_id or home_id == away_id:
                        skipped += 1
                        continue
                    _align_existing_source_match(
                        session,
                        source_id=source.id,
                        competition_season_id=competition_season.id,
                        dto=dto,
                        home_team_id=home_id,
                        away_team_id=away_id,
                        timezone=timezone,
                    )
                    result = upsert_match(
                        session,
                        dto,
                        competition_season_id=competition_season.id,
                        home_team_id=home_id,
                        away_team_id=away_id,
                    )
                    if result.inserted:
                        inserted += 1
                    elif result.updated_fields:
                        updated += 1
                    else:
                        skipped += 1
                competition_season.last_synced_at = now
        except Exception as error:
            failures += 1
            status = SyncStatus.FAILED.value
            message = f"{error.__class__.__name__}: {str(error)[:800]}"

        requests_delta = provider.request_count - request_start
        requests_count += requests_delta
        inserted_count += inserted
        updated_count += updated
        skipped_count += skipped
        error_count += int(status == SyncStatus.FAILED.value)
        with session_factory.begin() as session:
            session.add(
                SyncRunItem(
                    sync_run_id=run_id,
                    source_id=source.id,
                    competition_id=competition_id,
                    operation="PROMIEDOS_SEASON_FIXTURES",
                    status=status,
                    requests_count=requests_delta,
                    inserted_count=inserted,
                    updated_count=updated,
                    skipped_count=skipped,
                    error_count=int(status == SyncStatus.FAILED.value),
                    error_code="PROMIEDOS_FIXTURE_SYNC" if message else None,
                    message=message,
                )
            )
    status = (
        SyncStatus.FAILED
        if failures == len(definitions)
        else (SyncStatus.PARTIAL if failures else SyncStatus.SUCCESS)
    )
    message = (
        f"Promiedos fixtures: {inserted_count} partidos nuevos, {updated_count} actualizados, "
        f"{skipped_count} omitidos, {failures} errores"
    )
    with session_factory.begin() as session:
        run = session.get(SyncRun, run_id)
        if run:
            run.finished_at = datetime.now(UTC)
            run.status = status.value
            run.requests_count = requests_count
            run.inserted_count = inserted_count
            run.updated_count = updated_count
            run.skipped_count = skipped_count
            run.error_count = error_count
            run.summary = message
    return PromiedosMatchSyncSummary(
        str(run_id),
        status,
        requests_count,
        inserted_count,
        updated_count,
        skipped_count,
        error_count,
        message,
    )
