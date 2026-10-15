"""API-Football adapter for current player profiles and season statistics."""

from collections.abc import Callable
from typing import Any

from data90.config.settings import get_settings
from data90.domain.exceptions import ProviderAuthenticationError, ProviderError
from data90.providers.base import HttpProvider
from data90.sync.rate_limiter import SlidingWindowRateLimiter


class ApiFootballProvider(HttpProvider):
    source_code = "api-football"
    base_url = "https://v3.football.api-sports.io"
    _rate_limiter = SlidingWindowRateLimiter(limit=9, window_seconds=60)

    def __init__(
        self,
        token: str | None = None,
        *,
        request_budget: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.token = (
            token if token is not None else (get_settings().api_football_token.get_secret_value())
        )
        self.request_budget = request_budget

    def before_request(self) -> None:
        self._rate_limiter.acquire()
        if self.request_budget is not None:
            self.request_budget()

    def get_player_page(self, league_id: int, season: int, page: int) -> dict[str, Any]:
        if not self.token:
            raise ProviderAuthenticationError("Falta API_FOOTBALL_TOKEN en el archivo .env")
        result = self.request_json(
            "players",
            headers={"x-apisports-key": self.token},
            params={"league": league_id, "season": season, "page": page},
        )
        if not isinstance(result, dict):
            raise ProviderError("API-Football devolvió una estructura inesperada")
        errors = result.get("errors")
        if errors:
            if isinstance(errors, dict):
                message = ", ".join(str(value) for value in errors.values())
            else:
                message = str(errors)
            if "token" in message.casefold() or "key" in message.casefold():
                raise ProviderAuthenticationError("API-Football rechazó API_FOOTBALL_TOKEN")
            raise ProviderError(f"API-Football rechazó la consulta: {message[:300]}")
        if not isinstance(result.get("response"), list):
            raise ProviderError("API-Football no devolvió la lista de jugadores esperada")
        paging = result.get("paging")
        if not isinstance(paging, dict):
            raise ProviderError("API-Football no informó la paginación de jugadores")
        return result
