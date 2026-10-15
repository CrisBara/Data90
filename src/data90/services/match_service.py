from datetime import date

from sqlalchemy.orm import Session

from data90.config.settings import get_settings
from data90.repositories.matches import MatchListItem, matches_for_local_date


class MatchService:
    def __init__(self, session: Session, timezone_name: str | None = None) -> None:
        self.session = session
        self.timezone_name = timezone_name or get_settings().app_timezone

    def get_matches(
        self, selected_date: date, competition_codes: tuple[str, ...] | None = None
    ) -> list[MatchListItem]:
        return matches_for_local_date(
            self.session, selected_date, self.timezone_name, competition_codes
        )
