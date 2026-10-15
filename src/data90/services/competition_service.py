"""Read-only application service for competition summaries and seasons."""

from sqlalchemy.orm import Session

from data90.domain.exceptions import EntityResolutionError
from data90.repositories.competitions import (
    CompetitionListItem,
    CompetitionMatchItem,
    CompetitionSeasonItem,
    StandingItem,
    competition_by_code,
    competition_list,
    competition_seasons,
    matches_for_competition_season,
    standings_for_competition_season,
)


class CompetitionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_competitions(self) -> list[CompetitionListItem]:
        return competition_list(self.session)

    def get_seasons(self, competition_code: str) -> list[CompetitionSeasonItem]:
        competition = competition_by_code(self.session, competition_code)
        if competition is None:
            raise EntityResolutionError(f"Competición inexistente: {competition_code}")
        return competition_seasons(self.session, competition.id)

    def get_matches(self, competition_season_id: int) -> list[CompetitionMatchItem]:
        return matches_for_competition_season(self.session, competition_season_id)

    def get_standings(self, competition_season_id: int) -> list[StandingItem]:
        return standings_for_competition_season(self.session, competition_season_id)
