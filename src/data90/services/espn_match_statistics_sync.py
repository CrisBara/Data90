"""Import finished match box scores from ESPN's public soccer feed."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from data90.config.settings import get_settings
from data90.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    MatchTeamStatistic,
    Season,
    Team,
    TeamAlias,
)
from data90.providers.espn import ESPN_COMPETITION_SLUGS, ESPNProvider
from data90.sync.normalizer import normalize_name

SOURCE_CODE = "espn-soccer"
STAT_MAP = {
    "possessionPct": ("possession", "decimal"),
    "totalShots": ("shots_total", "int"),
    "shotsOnTarget": ("shots_on_target", "int"),
    "blockedShots": ("shots_blocked", "int"),
    "wonCorners": ("corners", "int"),
    "foulsCommitted": ("fouls", "int"),
    "offsides": ("offsides", "int"),
    "yellowCards": ("yellow_cards", "int"),
    "redCards": ("red_cards", "int"),
    "totalPasses": ("passes_total", "int"),
    "accuratePasses": ("passes_accurate", "int"),
    "saves": ("goalkeeper_saves", "int"),
}
IGNORED_SUFFIXES = {"fc", "afc", "cf", "sc", "ac", "club", "the"}


@dataclass(frozen=True, slots=True)
class ESPNMatchStatisticsSummary:
    status: str
    requests_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    error_count: int
    message: str


def _team_key(value: str) -> str:
    words = normalize_name(value).split()
    while words and words[-1] in IGNORED_SUFFIXES:
        words.pop()
    return " ".join(words)


def _number(value: object, kind: str) -> int | Decimal | None:
    if value in (None, "", "—", "-"):
        return None
    text = str(value).strip().replace("%", "")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return int(number) if kind == "int" else number


def _active_finished_matches(session: Session, competition_codes: set[str] | None) -> list[tuple]:
    statement = (
        select(Competition.code, Match, Team, Season)
        .join(CompetitionSeason, Match.competition_season_id == CompetitionSeason.id)
        .join(Competition, CompetitionSeason.competition_id == Competition.id)
        .join(Season, CompetitionSeason.season_id == Season.id)
        .join(Team, Match.home_team_id == Team.id)
        .where(
            Season.active.is_(True),
            Competition.active.is_(True),
            Match.status == "FINISHED",
            Match.home_score_ft.is_not(None),
            Match.away_score_ft.is_not(None),
            Competition.code.in_(tuple(ESPN_COMPETITION_SLUGS)),
        )
        .order_by(Competition.code, Match.kickoff_utc)
    )
    if competition_codes:
        statement = statement.where(Competition.code.in_(competition_codes))
    rows = list(session.execute(statement))
    result = []
    for code, match, _home, season in rows:
        home = session.get(Team, match.home_team_id)
        away = session.get(Team, match.away_team_id)
        if home and away:
            result.append((code, match, home, away, season))
    return result


def _name_matches(value: str, team_id: int, names_by_team: dict[int, set[str]]) -> bool:
    key = _team_key(value)
    return bool(key) and key in names_by_team.get(team_id, set())


def _event_teams(event: dict) -> tuple[str | None, str | None]:
    competition = (event.get("competitions") or [{}])[0]
    home = away = None
    for competitor in competition.get("competitors", []):
        team = competitor.get("team") or {}
        name = team.get("displayName") or team.get("name")
        if competitor.get("homeAway") == "home":
            home = name
        elif competitor.get("homeAway") == "away":
            away = name
    return home, away


def _boxscore_statistics(summary: dict) -> dict[str, dict[str, object]]:
    result = {}
    boxscore = summary.get("boxscore") or {}
    for team in boxscore.get("teams", []):
        display_name = team.get("displayName") or team.get("team", {}).get("displayName")
        values = {}
        for statistic in team.get("statistics", []):
            key = statistic.get("name") or statistic.get("abbreviation")
            if key in STAT_MAP:
                field, kind = STAT_MAP[key]
                values[field] = _number(statistic.get("displayValue", statistic.get("value")), kind)
        if display_name:
            result[display_name] = values
    return result


def sync_espn_match_statistics(
    session_factory,
    *,
    provider: ESPNProvider | None = None,
    competition_codes: tuple[str, ...] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ESPNMatchStatisticsSummary:
    """Sync active season matches, or limit to a date range for incremental runs."""
    provider = provider or ESPNProvider()
    inserted = updated = skipped = errors = 0
    messages: list[str] = []
    codes = set(competition_codes) if competition_codes else None
    timezone_name = get_settings().app_timezone
    with session_factory() as session:
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
                attribution_text="Estadísticas de partidos consultadas en ESPN",
                terms_url="https://www.espn.com/adoperations/marketingspecs/",
            )
            session.add(source)
            session.flush()
        alias_rows = session.execute(select(Team.canonical_name, Team.short_name, Team.id)).all()
        names_by_team: dict[int, set[str]] = {}
        for canonical, short, team_id in alias_rows:
            for name in (canonical, short):
                if name:
                    names_by_team.setdefault(team_id, set()).add(_team_key(name))
        for alias, team_id in session.execute(select(TeamAlias.alias, TeamAlias.team_id)):
            names_by_team.setdefault(team_id, set()).add(_team_key(alias))
        matches = _active_finished_matches(session, codes)
        if date_from:
            matches = [
                row
                for row in matches
                if row[1].kickoff_utc.astimezone(ZoneInfo(timezone_name)).date() >= date_from
            ]
        if date_to:
            matches = [
                row
                for row in matches
                if row[1].kickoff_utc.astimezone(ZoneInfo(timezone_name)).date() <= date_to
            ]
        grouped: dict[tuple[str, date], list[tuple]] = {}
        for row in matches:
            local_day = row[1].kickoff_utc.astimezone(ZoneInfo(timezone_name)).date()
            grouped.setdefault((row[0], local_day), []).append(row)

        for (code, match_day), fixtures in grouped.items():
            slug = ESPN_COMPETITION_SLUGS[code]
            try:
                scoreboard = provider.scoreboard(slug, match_day.strftime("%Y%m%d"))
                events = scoreboard.get("events") or []
                for event in events:
                    competition = (event.get("competitions") or [{}])[0]
                    completed = (
                        (competition.get("status") or {}).get("type", {}).get("completed", False)
                    )
                    if not completed:
                        continue
                    espn_home, espn_away = _event_teams(event)
                    candidates = [
                        fixture
                        for fixture in fixtures
                        if _name_matches(espn_home or "", fixture[2].id, names_by_team)
                        and _name_matches(espn_away or "", fixture[3].id, names_by_team)
                    ]
                    if len(candidates) != 1:
                        continue
                    local_match = candidates[0][1]
                    home_id = candidates[0][2].id
                    away_id = candidates[0][3].id
                    existing_rows = session.scalars(
                        select(MatchTeamStatistic).where(
                            MatchTeamStatistic.match_id == local_match.id,
                            MatchTeamStatistic.source_id == source.id,
                            MatchTeamStatistic.team_id.in_((home_id, away_id)),
                        )
                    ).all()
                    complete_team_ids = {
                        row.team_id
                        for row in existing_rows
                        if all(
                            getattr(row, field) is not None
                            for field in ("yellow_cards", "red_cards", "corners")
                        )
                    }
                    if complete_team_ids == {home_id, away_id}:
                        continue
                    event_id = str(event.get("id") or "")
                    if not event_id:
                        skipped += 1
                        continue
                    summary = provider.summary(slug, event_id)
                    stats = _boxscore_statistics(summary)
                    if not stats:
                        skipped += 1
                        continue
                    for espn_name, team_stats in stats.items():
                        matching_teams = [
                            team_id
                            for team_id in (home_id, away_id)
                            if _name_matches(espn_name, team_id, names_by_team)
                        ]
                        if len(matching_teams) != 1:
                            continue
                        team_id = matching_teams[0]
                        row = session.scalar(
                            select(MatchTeamStatistic).where(
                                MatchTeamStatistic.match_id == local_match.id,
                                MatchTeamStatistic.team_id == team_id,
                                MatchTeamStatistic.source_id == source.id,
                            )
                        )
                        if row is None:
                            row = MatchTeamStatistic(
                                match_id=local_match.id,
                                team_id=team_id,
                                source_id=source.id,
                            )
                            session.add(row)
                            inserted += 1
                        else:
                            updated += 1
                        for field, value in team_stats.items():
                            setattr(row, field, value)
                        row.source_updated_at = datetime.now(UTC)
                    if len(stats) < 2:
                        skipped += 1
                session.commit()
            except Exception as error:  # keep other competition dates running after a failure
                session.rollback()
                errors += 1
                messages.append(f"{code} {match_day}: {error.__class__.__name__}")
            # Count local fixtures that did not receive this provider's box score.
            matched_ids = set(
                session.scalars(
                    select(MatchTeamStatistic.match_id).where(
                        MatchTeamStatistic.source_id == source.id,
                        MatchTeamStatistic.match_id.in_([fixture[1].id for fixture in fixtures]),
                    )
                ).all()
            )
            skipped += sum(fixture[1].id not in matched_ids for fixture in fixtures)
    if errors and not inserted and not updated:
        status = "FAILED"
    elif errors:
        status = "PARTIAL"
    else:
        status = "SUCCESS"
    detail = "; ".join(messages[:8])
    message = (
        f"ESPN: {inserted} filas insertadas, {updated} actualizadas, "
        f"{skipped} partidos sin estadísticas, {errors} errores"
    )
    if detail:
        message += f". {detail}"
    return ESPNMatchStatisticsSummary(
        status, provider.request_count, inserted, updated, skipped, errors, message
    )
