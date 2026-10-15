from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from data90.config.constants import COMPETITIONS
from data90.config.settings import get_settings
from data90.domain.dto import (
    NormalizedCompetition,
    NormalizedMatch,
    NormalizedSeason,
    NormalizedStanding,
    NormalizedTeam,
)
from data90.domain.enums import MatchStatus
from data90.domain.exceptions import ProviderAuthenticationError, ProviderError
from data90.providers.base import HttpProvider
from data90.sync.rate_limiter import SlidingWindowRateLimiter

STATUS_MAP = {
    "TIMED": MatchStatus.SCHEDULED,
    "SCHEDULED": MatchStatus.SCHEDULED,
    "IN_PLAY": MatchStatus.LIVE,
    "PAUSED": MatchStatus.PAUSED,
    "FINISHED": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.SUSPENDED,
    "CANCELLED": MatchStatus.CANCELLED,
}


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class FootballDataProvider(HttpProvider):
    source_code = "football-data"
    base_url = "https://api.football-data.org/v4"
    _rate_limiter = SlidingWindowRateLimiter(limit=9, window_seconds=60)

    def __init__(self, token: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.token = (
            token if token is not None else get_settings().football_data_token.get_secret_value()
        )

    def before_request(self) -> None:
        self._rate_limiter.acquire()

    def _get(self, path: str, *, params: dict[str, object] | None = None) -> dict[str, Any]:
        if not self.token:
            raise ProviderAuthenticationError("Falta FOOTBALL_DATA_TOKEN en el entorno")
        result = self.request_json(path, headers={"X-Auth-Token": self.token}, params=params)
        if not isinstance(result, dict):
            raise ProviderError("football-data.org devolvió una estructura inesperada")
        return result

    @staticmethod
    def external_code(competition_code: str) -> str:
        for competition in COMPETITIONS:
            if competition.code == competition_code:
                for source, code in competition.provider_codes:
                    if source == "football-data":
                        return code
        raise ValueError(f"Competición no soportada por football-data.org: {competition_code}")

    def get_competition(self, competition_code: str) -> NormalizedCompetition:
        data = self._get(f"competitions/{self.external_code(competition_code)}")
        return NormalizedCompetition(
            self.source_code,
            str(data["id"]),
            data.get("code"),
            competition_code,
            data.get("name", competition_code),
        )

    def get_season(self, competition_code: str, season_start_year: int) -> NormalizedSeason:
        data = self._get(f"competitions/{self.external_code(competition_code)}")
        seasons = list(data.get("seasons", []))
        current_season = data.get("currentSeason")
        if isinstance(current_season, dict):
            seasons.append(current_season)
        season = next(
            (s for s in seasons if s.get("startDate", "").startswith(str(season_start_year))),
            None,
        )
        if season is None:
            raise ProviderError(
                f"No se encontró temporada {season_start_year} para {competition_code}"
            )
        end_year = int((season.get("endDate") or str(season_start_year))[:4])
        return NormalizedSeason(
            self.source_code,
            competition_code,
            str(season.get("id")) if season.get("id") else None,
            data.get("code"),
            str(season.get("id", season_start_year)),
            season_start_year,
            end_year,
            date.fromisoformat(season["startDate"]) if season.get("startDate") else None,
            date.fromisoformat(season["endDate"]) if season.get("endDate") else None,
        )

    def get_current_season(self, competition_code: str) -> NormalizedSeason:
        data = self._get(f"competitions/{self.external_code(competition_code)}")
        season = data.get("currentSeason")
        if not isinstance(season, dict) or not season.get("startDate"):
            raise ProviderError(f"No hay temporada activa publicada para {competition_code}")
        start_year = int(season["startDate"][:4])
        end_year = int((season.get("endDate") or str(start_year))[:4])
        return NormalizedSeason(
            source_code=self.source_code,
            competition_code=competition_code,
            external_id=str(season["id"]) if season.get("id") else None,
            external_code=data.get("code"),
            external_season=str(season.get("id", start_year)),
            start_year=start_year,
            end_year=end_year,
            start_date=date.fromisoformat(season["startDate"]),
            end_date=date.fromisoformat(season["endDate"]) if season.get("endDate") else None,
        )

    def get_team_details(self, team_external_id: str) -> dict[str, Any]:
        """Return the provider's current team profile and full published squad."""
        data = self._get(f"teams/{team_external_id}")
        if data.get("id") is None or not isinstance(data.get("squad"), list):
            raise ProviderError("football-data.org devolvió un equipo sin plantel válido")
        return data

    def get_teams(self, competition_code: str, season_start_year: int) -> Iterable[NormalizedTeam]:
        data = self._get(
            f"competitions/{self.external_code(competition_code)}/teams",
            params={"season": season_start_year},
        )
        return [
            NormalizedTeam(
                self.source_code,
                str(team["id"]),
                team["name"],
                team.get("shortName"),
                team.get("tla"),
                team.get("area", {}).get("name"),
                None,
                team.get("venue"),
                _int(team.get("founded")),
                team.get("crest"),
            )
            for team in data.get("teams", [])
        ]

    def get_matches(
        self, competition_code: str, season_start_year: int, date_from: date, date_to: date
    ) -> Iterable[NormalizedMatch]:
        data = self._get(
            f"competitions/{self.external_code(competition_code)}/matches",
            params={
                "season": season_start_year,
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
            },
        )
        matches = []
        for item in data.get("matches", []):
            home, away = item.get("homeTeam") or {}, item.get("awayTeam") or {}
            kickoff = _dt(item.get("utcDate"))
            if kickoff is None:
                raise ProviderError(f"Partido {item.get('id', 'sin id')} no tiene utcDate")
            score = item.get("score") or {}
            full, half, extra, penalties = (
                score.get(key) or {} for key in ("fullTime", "halfTime", "extraTime", "penalties")
            )
            matches.append(
                NormalizedMatch(
                    source_code=self.source_code,
                    external_id=str(item["id"]),
                    competition_code=competition_code,
                    season_start_year=season_start_year,
                    kickoff_utc=kickoff,
                    home_external_id=str(home.get("id", "")),
                    away_external_id=str(away.get("id", "")),
                    home_name=home.get("name", ""),
                    away_name=away.get("name", ""),
                    status=STATUS_MAP.get(item.get("status"), MatchStatus.UNKNOWN),
                    matchday=_int(item.get("matchday")),
                    round_label=item.get("matchday") and f"Matchday {item['matchday']}",
                    stage=item.get("stage"),
                    group_name=item.get("group"),
                    home_score_ft=_int(full.get("home")),
                    away_score_ft=_int(full.get("away")),
                    home_score_ht=_int(half.get("home")),
                    away_score_ht=_int(half.get("away")),
                    home_score_et=_int(extra.get("home")),
                    away_score_et=_int(extra.get("away")),
                    home_score_pen=_int(penalties.get("home")),
                    away_score_pen=_int(penalties.get("away")),
                    source_updated_at=_dt(item.get("lastUpdated")),
                    venue_name=item.get("venue"),
                )
            )
        return matches

    def get_standings(
        self, competition_code: str, season_start_year: int
    ) -> Iterable[NormalizedStanding]:
        data = self._get(
            f"competitions/{self.external_code(competition_code)}/standings",
            params={"season": season_start_year},
        )
        result = []
        for table in data.get("standings", []):
            for row in table.get("table", []):
                result.append(
                    NormalizedStanding(
                        self.source_code,
                        competition_code,
                        season_start_year,
                        str(row["team"]["id"]),
                        row["position"],
                        row["playedGames"],
                        row["won"],
                        row["draw"],
                        row["lost"],
                        row["goalsFor"],
                        row["goalsAgainst"],
                        row["goalDifference"],
                        row["points"],
                        table.get("type", "TOTAL"),
                        table.get("group"),
                        table.get("matchday"),
                        row.get("form"),
                    )
                )
        return result
