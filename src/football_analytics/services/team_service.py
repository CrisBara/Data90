from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from football_analytics.analytics.team_metrics import (
    TeamMatchResult,
    TeamMetrics,
    calculate_team_metrics,
)
from football_analytics.database.models import Match, Team
from football_analytics.domain.exceptions import EntityResolutionError
from football_analytics.repositories.teams import (
    TeamListItem,
    TeamMatchItem,
    search_teams,
    team_match_history,
)


@dataclass(frozen=True, slots=True)
class TeamProfile:
    team_id: int
    name: str
    country_name: str | None
    metrics: TeamMetrics
    match_history: list[TeamMatchItem]


class TeamService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(
        self, query: str | None = None, competition_code: str | None = None
    ) -> list[TeamListItem]:
        return search_teams(self.session, query, competition_code)

    def get_match_history(self, team_id: int) -> list[TeamMatchItem]:
        if self.session.get(Team, team_id) is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")
        return team_match_history(self.session, team_id)

    def get_profile(self, team_id: int) -> TeamProfile:
        team = self.session.get(Team, team_id)
        if team is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")
        return TeamProfile(
            team_id=team.id,
            name=team.canonical_name,
            country_name=team.country_name,
            metrics=self.get_metrics(team_id),
            match_history=team_match_history(self.session, team_id),
        )

    def get_metrics(self, team_id: int, competition_season_id: int | None = None) -> TeamMetrics:
        if self.session.get(Team, team_id) is None:
            raise EntityResolutionError(f"Equipo inexistente: {team_id}")
        statement = select(Match).where(
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            Match.status == "FINISHED",
            Match.home_score_ft.is_not(None),
            Match.away_score_ft.is_not(None),
        )
        if competition_season_id is not None:
            statement = statement.where(Match.competition_season_id == competition_season_id)
        records = []
        for match in self.session.scalars(statement).all():
            is_home = match.home_team_id == team_id
            records.append(
                TeamMatchResult(
                    kickoff_utc=match.kickoff_utc,
                    goals_for=match.home_score_ft if is_home else match.away_score_ft,
                    goals_against=match.away_score_ft if is_home else match.home_score_ft,
                    is_home=is_home,
                )
            )
        return calculate_team_metrics(records)
