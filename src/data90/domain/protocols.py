from collections.abc import Iterable
from datetime import date
from typing import Protocol

from data90.domain.dto import (
    NormalizedCompetition,
    NormalizedMatch,
    NormalizedSeason,
    NormalizedStanding,
    NormalizedTeam,
)


class FootballDataProvider(Protocol):
    source_code: str

    def get_competition(self, competition_code: str) -> NormalizedCompetition: ...

    def get_season(self, competition_code: str, season_start_year: int) -> NormalizedSeason: ...

    def get_current_season(self, competition_code: str) -> NormalizedSeason: ...

    def get_teams(
        self, competition_code: str, season_start_year: int
    ) -> Iterable[NormalizedTeam]: ...

    def get_matches(
        self, competition_code: str, season_start_year: int, date_from: date, date_to: date
    ) -> Iterable[NormalizedMatch]: ...

    def get_standings(
        self, competition_code: str, season_start_year: int
    ) -> Iterable[NormalizedStanding]: ...
