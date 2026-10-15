"""Import free current-season match statistics from Football-Data.co.uk CSVs."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.config.constants import COMPETITIONS, SOURCES
from data90.config.settings import get_settings
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchTeamStatistic,
    Season,
    SyncRun,
    SyncRunItem,
    Team,
    TeamAlias,
)
from data90.domain.enums import SyncStatus
from data90.sync.orchestrator import SyncSummary

SessionFactory = Callable[[], Session]

_DIVISIONS = {
    "PL": "E0",
    "LALIGA": "SP1",
    "SERIE_A": "I1",
    "BUNDESLIGA": "D1",
    "LIGUE_1": "F1",
    "EREDIVISIE": "N1",
}

# football-data.co.uk abbreviates these club names. Each target is a canonical
# name used by the corresponding competition in this application.
_CLUB_NAME_OVERRIDES = {
    "PL": {
        "brighton": "Brighton & Hove Albion FC",
        "coventry": "Coventry City FC",
        "hull": "Hull City AFC",
        "ipswich": "Ipswich Town FC",
        "nottmforest": "Nottingham Forest FC",
    },
    "LALIGA": {
        "athbilbao": "Athletic Club",
        "athmadrid": "Club Atlético de Madrid",
        "espanol": "RCD Espanyol de Barcelona",
        "lacoruna": "RC Deportivo La Coruña",
        "ladepor": "RC Deportivo La Coruña",
        "deportivo": "RC Deportivo La Coruña",
        "sociedad": "Real Sociedad de Fútbol",
        "vallecano": "Rayo Vallecano de Madrid",
    },
    "BUNDESLIGA": {
        "einfrankfurt": "Eintracht Frankfurt",
        "fckoln": "1. FC Köln",
        "hamburg": "Hamburger SV",
        "werderbremen": "SV Werder Bremen",
    },
    "LIGUE_1": {"parissg": "Paris Saint-Germain FC"},
    "EREDIVISIE": {"forsittard": "Fortuna Sittard"},
}

_STAT_COLUMNS = {
    "shots_total": ("HS", "AS"),
    "shots_on_target": ("HST", "AST"),
    "fouls": ("HF", "AF"),
    "corners": ("HC", "AC"),
    "yellow_cards": ("HY", "AY"),
    "red_cards": ("HR", "AR"),
}


@dataclass(slots=True)
class _CompetitionResult:
    code: str
    competition_id: int | None
    requests: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] | None = None

    def __post_init__(self) -> None:
        self.details = []


def _normalize(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", plain.casefold())


def _number(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _fetch_rows(division: str) -> list[dict[str, str]]:
    season_code = f"{datetime.now(UTC).year % 100:02d}{(datetime.now(UTC).year + 1) % 100:02d}"
    url = f"https://www.football-data.co.uk/mmz4281/{season_code}/{division}.csv"
    response = requests.get(url, timeout=25, headers={"User-Agent": "Data90/1.0"})
    response.raise_for_status()
    content = response.content.decode("cp1252", errors="replace").lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows or not {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}.issubset(rows[0]):
        raise ValueError("El CSV no contiene las columnas de partidos esperadas")
    return rows


def _target_season(session: Session, competition_id: int) -> CompetitionSeason | None:
    local_today = datetime.now(UTC).astimezone(ZoneInfo(get_settings().app_timezone)).date()
    start_year = local_today.year if local_today.month >= 7 else local_today.year - 1
    return session.scalar(
        select(CompetitionSeason)
        .join(Season, CompetitionSeason.season_id == Season.id)
        .where(CompetitionSeason.competition_id == competition_id, Season.start_year == start_year)
    )


def _team_names(session: Session, team_ids: set[int]) -> tuple[dict[str, set[int]], dict[str, int]]:
    aliases: dict[str, set[int]] = defaultdict(set)
    canonical: dict[str, int] = {}
    if not team_ids:
        return aliases, canonical
    teams = session.execute(
        select(Team.id, Team.canonical_name, Team.short_name).where(Team.id.in_(team_ids))
    ).all()
    for team_id, name, short_name in teams:
        canonical[_normalize(name)] = team_id
        aliases[_normalize(name)].add(team_id)
        if short_name:
            aliases[_normalize(short_name)].add(team_id)
    for team_id, alias in session.execute(
        select(TeamAlias.team_id, TeamAlias.alias).where(TeamAlias.team_id.in_(team_ids))
    ):
        aliases[_normalize(alias)].add(team_id)
    return aliases, canonical


def _resolve_team(
    code: str, name: str, aliases: dict[str, set[int]], canonical: dict[str, int]
) -> int | None:
    key = _normalize(name)
    override = _CLUB_NAME_OVERRIDES.get(code, {}).get(key)
    if override:
        return canonical.get(_normalize(override))
    matches = aliases.get(key, set())
    return next(iter(matches)) if len(matches) == 1 else None


def _source(session: Session) -> DataSource:
    definition = next(row for row in SOURCES if row["code"] == "football-data-uk")
    source = session.scalar(select(DataSource).where(DataSource.code == definition["code"]))
    if source is None:
        source = DataSource(**definition)
        session.add(source)
        session.flush()
    else:
        for key, value in definition.items():
            setattr(source, key, value)
    return source


def sync_football_data_uk_statistics(
    session_factory: SessionFactory,
    *,
    competition_codes: tuple[str, ...] | None = None,
) -> SyncSummary:
    """Fill match-level team-stat gaps from the public current-season CSV files.

    Only completed fixtures whose competition, home/away clubs, local match date,
    and final score all match the database are imported. Existing provider rows
    are preserved; this provider is registered at a lower priority as a fallback.
    """
    selected = set(competition_codes) if competition_codes else set(_DIVISIONS)
    now = datetime.now(UTC)
    results = [
        _CompetitionResult(code, None)
        for code in _DIVISIONS
        if code in selected
    ]
    fetched: dict[str, list[dict[str, str]]] = {}
    for result in results:
        try:
            fetched[result.code] = _fetch_rows(_DIVISIONS[result.code])
            result.requests = 1
        except Exception as exc:  # keep other competitions updating if one feed is unavailable
            result.errors += 1
            result.details.append(f"{result.code}: {type(exc).__name__}: {exc}")
            result.requests = 1

    with session_factory() as session:
        run = SyncRun(mode="MANUAL", status=SyncStatus.RUNNING.value, started_at=now)
        session.add(run)
        session.flush()
        source = _source(session)
        source_id = source.id
        source_ids = {source.code: source_id}
        for result in results:
            competition = session.scalar(select(Competition).where(Competition.code == result.code))
            result.competition_id = competition.id if competition else None
            rows = fetched.get(result.code, [])
            if result.code not in fetched:
                continue
            if competition is None:
                result.errors += 1
                result.details.append(f"{result.code}: competición no registrada")
                continue
            competition_season = _target_season(session, competition.id)
            if competition_season is None:
                result.errors += 1
                result.details.append(f"{result.code}: temporada actual no registrada")
                continue

            matches = session.scalars(
                select(Match).where(Match.competition_season_id == competition_season.id)
            ).all()
            team_ids = {team_id for match in matches for team_id in (match.home_team_id, match.away_team_id)}
            aliases, canonical = _team_names(session, team_ids)
            match_by_key: dict[tuple[int, int, date], list[Match]] = defaultdict(list)
            for match in matches:
                local_date = match.kickoff_utc.astimezone(ZoneInfo("Europe/London")).date()
                match_by_key[(match.home_team_id, match.away_team_id, local_date)].append(match)

            for row in rows:
                home_score = _number(row.get("FTHG"))
                away_score = _number(row.get("FTAG"))
                if home_score is None or away_score is None:
                    continue
                date_text = (row.get("Date") or "").strip()
                try:
                    try:
                        match_date = datetime.strptime(date_text, "%d/%m/%Y").date()
                    except ValueError:
                        match_date = datetime.strptime(date_text, "%d/%m/%y").date()
                except ValueError:
                    result.skipped += 1
                    continue
                home_id = _resolve_team(result.code, row.get("HomeTeam", ""), aliases, canonical)
                away_id = _resolve_team(result.code, row.get("AwayTeam", ""), aliases, canonical)
                if home_id is None or away_id is None:
                    result.skipped += 1
                    continue
                candidates = match_by_key.get((home_id, away_id, match_date), [])
                candidates = [
                    match
                    for match in candidates
                    if match.status == "FINISHED"
                    and match.home_score_ft == home_score
                    and match.away_score_ft == away_score
                ]
                if len(candidates) != 1:
                    result.skipped += 1
                    continue
                match = candidates[0]
                for team_id, side in ((home_id, "home"), (away_id, "away")):
                    fields: dict[str, int] = {}
                    side_index = 0 if side == "home" else 1
                    for field, columns in _STAT_COLUMNS.items():
                        value = _number(row.get(columns[side_index]))
                        if value is not None:
                            fields[field] = value
                    if not fields:
                        continue
                    statistic = session.scalar(
                        select(MatchTeamStatistic).where(
                            MatchTeamStatistic.match_id == match.id,
                            MatchTeamStatistic.team_id == team_id,
                            MatchTeamStatistic.source_id == source_id,
                        )
                    )
                    if statistic is None:
                        statistic = MatchTeamStatistic(
                            match_id=match.id,
                            team_id=team_id,
                            source_id=source_id,
                            source_updated_at=now,
                        )
                        is_new = True
                    else:
                        is_new = False
                    changed = statistic.id is None
                    for field, value in fields.items():
                        if getattr(statistic, field) != value:
                            setattr(statistic, field, value)
                            changed = True
                    if changed:
                        if is_new:
                            result.inserted += 1
                        else:
                            result.updated += 1
                        statistic.source_updated_at = now
                        session.add(statistic)
                    else:
                        result.skipped += 1

            session.flush()

        details = "; ".join(item for result in results for item in (result.details or []))
        for result in results:
            session.add(
                SyncRunItem(
                    sync_run_id=run.id,
                    source_id=source_id,
                    competition_id=result.competition_id,
                    operation="MATCH_TEAM_STATISTICS",
                    status=(SyncStatus.PARTIAL.value if result.errors else SyncStatus.SUCCESS.value),
                    requests_count=result.requests,
                    inserted_count=result.inserted,
                    updated_count=result.updated,
                    skipped_count=result.skipped,
                    error_count=result.errors,
                    error_code="CSV_IMPORT_ERROR" if result.errors else None,
                    message=(
                        f"{result.code}: {result.inserted} filas nuevas, {result.updated} filas actualizadas, "
                        f"{result.skipped} omitidas, {result.errors} errores"
                    ),
                )
            )
        run.finished_at = datetime.now(UTC)
        run.status = (
            SyncStatus.PARTIAL.value
            if any(result.errors for result in results)
            else SyncStatus.SUCCESS.value
        )
        run.requests_count = sum(result.requests for result in results)
        run.inserted_count = sum(result.inserted for result in results)
        run.updated_count = sum(result.updated for result in results)
        run.skipped_count = sum(result.skipped for result in results)
        run.error_count = sum(result.errors for result in results)
        run.summary = details or "Football-Data.co.uk: importación CSV completada con cruces exactos"
        session.commit()

    error_count = sum(result.errors for result in results)
    status = SyncStatus.PARTIAL if error_count else SyncStatus.SUCCESS
    return SyncSummary(
        run_id=run.id,
        status=status,
        requests_count=sum(result.requests for result in results),
        inserted_count=sum(result.inserted for result in results),
        updated_count=sum(result.updated for result in results),
        skipped_count=sum(result.skipped for result in results),
        conflict_count=0,
        error_count=error_count,
        message=details or "Football-Data.co.uk: estadísticas de partidos sincronizadas",
    )
