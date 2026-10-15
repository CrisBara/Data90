"""Free TheSportsDB v1 player metadata API."""

from time import monotonic, sleep

from football_analytics.providers.base import HttpProvider


class TheSportsDBProvider(HttpProvider):
    """Public free API for crowd-sourced player profiles and metadata."""

    source_code = "thesportsdb"
    base_url = "https://www.thesportsdb.com/api/v1/json/123"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._last_request_at = 0.0

    def before_request(self) -> None:
        # Free tier documents a 30 requests/minute cap.
        remaining = 2.1 - (monotonic() - self._last_request_at)
        if remaining > 0:
            sleep(remaining)
        self._last_request_at = monotonic()

    def search_player(self, name: str) -> list[dict]:
        payload = self.request_json("searchplayers.php", params={"p": name})
        players = payload.get("player") if isinstance(payload, dict) else None
        return (
            [row for row in players if isinstance(row, dict)] if isinstance(players, list) else []
        )
