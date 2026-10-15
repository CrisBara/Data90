"""Database queries for team lists and match histories."""

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import case, func, select, union_all
from sqlalchemy.orm import Session, aliased

from data90.database.models import Competition, CompetitionSeason, Match, Season, Team


class TeamListItem(NamedTuple):
    team_id: int
    name: str
    country_name: str | None
    match_count: int
    competition_count: int
    latest_match_at: datetime | None


class TeamMatchItem(NamedTuple):
    kickoff_utc: datetime
    competition_name: str
    season_label: str
    opponent: str
    venue: str
    status: str
    goals_for: int | None
    goals_against: int | None
    matchday: int | None
    stage: str | None
    opponent_id: int
    competition_season_id: int
    round_label: str | None
    opponent_crest_url: str | None = None


def search_teams(
    session: Session, query: str | None = None, competition_code: str | None = None
) -> list[TeamListItem]:
    appearances = union_all(
        select(
            Match.home_team_id.label("team_id"),
            Match.id.label("match_id"),
            Match.kickoff_utc.label("kickoff_utc"),
            CompetitionSeason.competition_id.label("competition_id"),
        ).join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id),
        select(
            Match.away_team_id.label("team_id"),
            Match.id.label("match_id"),
            Match.kickoff_utc.label("kickoff_utc"),
            CompetitionSeason.competition_id.label("competition_id"),
        ).join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id),
    ).subquery()
    stats = (
        select(
            appearances.c.team_id,
            func.count(func.distinct(appearances.c.match_id)).label("match_count"),
            func.count(func.distinct(appearances.c.competition_id)).label("competition_count"),
            func.max(appearances.c.kickoff_utc).label("latest_match_at"),
        )
        .group_by(appearances.c.team_id)
        .subquery()
    )
    statement = (
        select(
            Team.id,
            Team.canonical_name,
            Team.country_name,
            func.coalesce(stats.c.match_count, 0),
            func.coalesce(stats.c.competition_count, 0),
            stats.c.latest_match_at,
        )
        .outerjoin(stats, stats.c.team_id == Team.id)
        .where(Team.active.is_(True))
        .order_by(Team.canonical_name)
    )
    if query and query.strip():
        statement = statement.where(Team.canonical_name.ilike(f"%{query.strip()}%"))
    if competition_code:
        matching_teams = union_all(
            select(Match.home_team_id.label("team_id"))
            .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
            .join(Competition, Competition.id == CompetitionSeason.competition_id)
            .where(Competition.code == competition_code),
            select(Match.away_team_id.label("team_id"))
            .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
            .join(Competition, Competition.id == CompetitionSeason.competition_id)
            .where(Competition.code == competition_code),
        ).subquery()
        statement = statement.where(Team.id.in_(select(matching_teams.c.team_id)))
    return [TeamListItem(*row) for row in session.execute(statement).all()]


def team_match_history(
    session: Session,
    team_id: int,
    *,
    competition_season_id: int | None = None,
    status: str | None = None,
    limit: int | None = None,
    upcoming: bool = False,
) -> list[TeamMatchItem]:
    home = aliased(Team, name="home_team")
    away = aliased(Team, name="away_team")
    is_home = Match.home_team_id == team_id
    rows = session.execute(
        select(
            Match.kickoff_utc,
            Competition.name,
            Season.label,
            case((is_home, away.canonical_name), else_=home.canonical_name),
            case((is_home, "Local"), else_="Visitante"),
            Match.status,
            case((is_home, Match.home_score_ft), else_=Match.away_score_ft),
            case((is_home, Match.away_score_ft), else_=Match.home_score_ft),
            Match.matchday,
            Match.stage,
            case((is_home, away.id), else_=home.id),
            Match.competition_season_id,
            Match.round_label,
            case((is_home, away.crest_url), else_=home.crest_url),
        )
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .join(CompetitionSeason, CompetitionSeason.id == Match.competition_season_id)
        .join(Competition, Competition.id == CompetitionSeason.competition_id)
        .join(Season, Season.id == CompetitionSeason.season_id)
        .where(is_home | (Match.away_team_id == team_id))
        .order_by(Match.kickoff_utc.asc() if upcoming else Match.kickoff_utc.desc())
    ).all()
    if competition_season_id is not None:
        # Projection index 11 is competition_season_id; row[-2] is round_label.
        rows = [row for row in rows if row[11] == competition_season_id]
    if status is not None:
        rows = [row for row in rows if row[5] == status]
    if limit is not None:
        rows = rows[:limit]
    return [TeamMatchItem(*row) for row in rows]
