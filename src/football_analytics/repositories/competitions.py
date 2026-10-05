"""Database queries backing the competitions list and detail views."""

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session, aliased

from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataSource,
    Match,
    Season,
    StandingRow,
    StandingsSnapshot,
    Team,
)


class CompetitionSeasonItem(NamedTuple):
    season_id: int
    competition_season_id: int
    label: str
    status: str
    team_count: int
    match_count: int
    last_synced_at: datetime | None


class CompetitionListItem(NamedTuple):
    competition_id: int
    code: str
    name: str
    country_name: str | None
    region_name: str | None
    competition_type: str | None
    latest_season: str | None
    team_count: int
    match_count: int
    last_synced_at: datetime | None


class CompetitionMatchItem(NamedTuple):
    kickoff_utc: datetime
    home_team: str
    away_team: str
    status: str
    home_score_ft: int | None
    away_score_ft: int | None
    matchday: int | None
    stage: str | None


class StandingItem(NamedTuple):
    position: int
    team_name: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: str | None


def competition_seasons(session: Session, competition_id: int) -> list[CompetitionSeasonItem]:
    team_refs = union_all(
        select(Match.competition_season_id, Match.home_team_id.label("team_id")),
        select(Match.competition_season_id, Match.away_team_id.label("team_id")),
    ).subquery()
    team_counts = (
        select(
            team_refs.c.competition_season_id,
            func.count(func.distinct(team_refs.c.team_id)).label("team_count"),
        )
        .group_by(team_refs.c.competition_season_id)
        .subquery()
    )
    match_counts = (
        select(Match.competition_season_id, func.count(Match.id).label("match_count"))
        .group_by(Match.competition_season_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Season.id,
            CompetitionSeason.id,
            Season.label,
            CompetitionSeason.status,
            func.coalesce(team_counts.c.team_count, 0),
            func.coalesce(match_counts.c.match_count, 0),
            CompetitionSeason.last_synced_at,
        )
        .join(CompetitionSeason, CompetitionSeason.season_id == Season.id)
        .outerjoin(team_counts, team_counts.c.competition_season_id == CompetitionSeason.id)
        .outerjoin(match_counts, match_counts.c.competition_season_id == CompetitionSeason.id)
        .where(CompetitionSeason.competition_id == competition_id)
        .order_by(Season.start_year.desc())
    ).all()
    return [CompetitionSeasonItem(*row) for row in rows]


def competition_list(session: Session) -> list[CompetitionListItem]:
    result: list[CompetitionListItem] = []
    for competition in session.scalars(
        select(Competition).where(Competition.active.is_(True)).order_by(Competition.display_order)
    ).all():
        seasons = competition_seasons(session, competition.id)
        latest = seasons[0] if seasons else None
        result.append(
            CompetitionListItem(
                competition.id,
                competition.code,
                competition.name,
                competition.country_name,
                competition.region_name,
                competition.competition_type,
                latest.label if latest else None,
                latest.team_count if latest else 0,
                latest.match_count if latest else 0,
                latest.last_synced_at if latest else None,
            )
        )
    return result


def competition_by_code(session: Session, code: str) -> Competition | None:
    return session.scalar(select(Competition).where(Competition.code == code))


def matches_for_competition_season(
    session: Session, competition_season_id: int
) -> list[CompetitionMatchItem]:
    home = aliased(Team, name="home_team")
    away = aliased(Team, name="away_team")
    rows = session.execute(
        select(
            Match.kickoff_utc,
            home.canonical_name,
            away.canonical_name,
            Match.status,
            Match.home_score_ft,
            Match.away_score_ft,
            Match.matchday,
            Match.stage,
        )
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .where(Match.competition_season_id == competition_season_id)
        .order_by(Match.kickoff_utc)
    ).all()
    return [CompetitionMatchItem(*row) for row in rows]


def standings_for_competition_season(
    session: Session, competition_season_id: int
) -> list[StandingItem]:
    latest_snapshot = session.scalar(
        select(StandingsSnapshot.id)
        .join(DataSource, DataSource.id == StandingsSnapshot.source_id)
        .where(StandingsSnapshot.competition_season_id == competition_season_id)
        .order_by(DataSource.priority, StandingsSnapshot.snapshot_at.desc())
        .limit(1)
    )
    if latest_snapshot is None:
        return []
    rows = session.execute(
        select(
            StandingRow.position,
            Team.canonical_name,
            StandingRow.played,
            StandingRow.won,
            StandingRow.drawn,
            StandingRow.lost,
            StandingRow.goals_for,
            StandingRow.goals_against,
            StandingRow.goal_difference,
            StandingRow.points,
            StandingRow.form,
        )
        .join(Team, Team.id == StandingRow.team_id)
        .where(StandingRow.snapshot_id == latest_snapshot)
        .order_by(StandingRow.position)
    ).all()
    return [StandingItem(*row) for row in rows]
