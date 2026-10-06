"""On-demand competition and team statistics from completed local matches."""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from football_analytics.analytics.team_metrics import (
    TeamMatchResult,
    TeamMetrics,
    calculate_team_metrics,
)
from football_analytics.database.models import Match, Team


@dataclass(frozen=True, slots=True)
class TeamStatistics:
    team_id: int
    team_name: str
    metrics: TeamMetrics


class StatisticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def for_competition_season(self, competition_season_id: int) -> list[TeamStatistics]:
        teams = self.session.execute(
            select(Team.id, Team.canonical_name)
            .join(
                Match,
                or_(Match.home_team_id == Team.id, Match.away_team_id == Team.id),
            )
            .where(Match.competition_season_id == competition_season_id)
            .distinct()
            .order_by(Team.canonical_name)
        ).all()
        matches_by_team: dict[int, list[TeamMatchResult]] = defaultdict(list)
        matches = self.session.execute(
            select(
                Match.home_team_id,
                Match.away_team_id,
                Match.kickoff_utc,
                Match.home_score_ft,
                Match.away_score_ft,
            ).where(
                Match.competition_season_id == competition_season_id,
                Match.status == "FINISHED",
                Match.home_score_ft.is_not(None),
                Match.away_score_ft.is_not(None),
            )
        ).all()
        for home_id, away_id, kickoff, home_score, away_score in matches:
            matches_by_team[home_id].append(
                TeamMatchResult(kickoff, home_score, away_score, is_home=True)
            )
            matches_by_team[away_id].append(
                TeamMatchResult(kickoff, away_score, home_score, is_home=False)
            )

        result = []
        for team_id, team_name in teams:
            result.append(
                TeamStatistics(
                    team_id=team_id,
                    team_name=team_name,
                    metrics=calculate_team_metrics(matches_by_team[team_id]),
                )
            )
        return result
