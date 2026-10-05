"""On-demand competition and team statistics from completed local matches."""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from football_analytics.analytics.team_metrics import TeamMetrics
from football_analytics.database.models import Match, Team
from football_analytics.services.team_service import TeamService


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
        team_service = TeamService(self.session)
        result = []
        for team_id, team_name in teams:
            result.append(
                TeamStatistics(
                    team_id=team_id,
                    team_name=team_name,
                    metrics=team_service.get_metrics(team_id, competition_season_id),
                )
            )
        return result
