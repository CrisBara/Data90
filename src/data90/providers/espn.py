"""Public ESPN soccer scoreboard and box-score feed."""

from time import monotonic, sleep

from data90.providers.base import HttpProvider

ESPN_COMPETITION_SLUGS = {
    "PL": "eng.1",
    "LALIGA": "esp.1",
    "SERIE_A": "ita.1",
    "BUNDESLIGA": "ger.1",
    "LIGUE_1": "fra.1",
    "EREDIVISIE": "ned.1",
    "UCL": "uefa.champions",
    "UEL": "uefa.europa",
}


class ESPNProvider(HttpProvider):
    """Small, rate-limited adapter for ESPN's currently public soccer feed."""

    source_code = "espn-soccer"
    base_url = "https://site.api.espn.com/apis/site/v2/sports/soccer"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._last_request_at = 0.0

    def before_request(self) -> None:
        remaining = 0.2 - (monotonic() - self._last_request_at)
        if remaining > 0:
            sleep(remaining)
        self._last_request_at = monotonic()

    def scoreboard(self, slug: str, match_date: str) -> dict:
        return self.request_json(f"{slug}/scoreboard", params={"dates": match_date})

    def summary(self, slug: str, event_id: str) -> dict:
        return self.request_json(f"{slug}/summary", params={"event": event_id})

    def teams(self, slug: str) -> dict:
        return self.request_json(f"{slug}/teams", params={"limit": 1000})

    def team_roster(self, slug: str, team_id: str) -> dict:
        return self.request_json(f"{slug}/teams/{team_id}/roster")
