from datetime import date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, aliased

from data90.database.models import Competition, CompetitionSeason, Match, Season, Team


class MatchListItem(NamedTuple):
    match_id: int
    competition_code: str
    competition_name: str
    season_label: str
    kickoff_utc: datetime
    home_team: str
    away_team: str
    status: str
    home_score_ft: int | None
    away_score_ft: int | None
    matchday: int | None
    stage: str | None
    home_team_id: int
    away_team_id: int
    home_crest_url: str | None = None
    away_crest_url: str | None = None


def matches_for_local_date(
    session: Session,
    selected_date: date,
    timezone_name: str,
    competition_codes: tuple[str, ...] | None = None,
) -> list[MatchListItem]:
    """Read matches only from PostgreSQL/SQLAlchemy, bounded by the selected local day."""
    timezone = ZoneInfo(timezone_name)
    start_local = datetime.combine(selected_date, datetime.min.time(), tzinfo=timezone)
    end_local = datetime.combine(
        selected_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone
    )
    home_team = aliased(Team, name="home_team")
    away_team = aliased(Team, name="away_team")
    statement: Select = (
        select(
            Match.id,
            Competition.code,
            Competition.name,
            Season.label,
            Match.kickoff_utc,
            home_team.canonical_name,
            away_team.canonical_name,
            Match.status,
            Match.home_score_ft,
            Match.away_score_ft,
            Match.matchday,
            Match.stage,
            Match.home_team_id,
            Match.away_team_id,
            home_team.crest_url,
            away_team.crest_url,
        )
        .join(CompetitionSeason, Match.competition_season_id == CompetitionSeason.id)
        .join(Competition, CompetitionSeason.competition_id == Competition.id)
        .join(Season, CompetitionSeason.season_id == Season.id)
        .join(home_team, Match.home_team_id == home_team.id)
        .join(away_team, Match.away_team_id == away_team.id)
        .where(Match.kickoff_utc >= start_local.astimezone(ZoneInfo("UTC")))
        .where(Match.kickoff_utc < end_local.astimezone(ZoneInfo("UTC")))
        .order_by(Match.kickoff_utc, Competition.display_order, home_team.canonical_name)
    )
    if competition_codes is not None:
        if not competition_codes:
            return []
        statement = statement.where(Competition.code.in_(competition_codes))
    rows = session.execute(statement).all()
    results = []
    for row in rows:
        results.append(
            MatchListItem(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                row[15],
            )
        )
    return results
