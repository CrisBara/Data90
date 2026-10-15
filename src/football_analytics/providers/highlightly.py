"""Highlightly Football API client for player discovery and validation."""

from typing import Any

from football_analytics.config.settings import get_settings
from football_analytics.domain.exceptions import ProviderAuthenticationError, ProviderError
from football_analytics.providers.base import HttpProvider


class HighlightlyProvider(HttpProvider):
    source_code = "highlightly"
    base_url = "https://soccer.highlightly.net"

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = (
            api_key
            if api_key is not None
            else get_settings().highlightly_api_key.get_secret_value()
        )

    def _get(self, path: str, *, params: dict[str, object] | None = None) -> Any:
        if not self.api_key:
            raise ProviderAuthenticationError("Falta HIGHLIGHTLY_API_KEY en el archivo .env")
        try:
            return self.request_json(
                path,
                headers={"x-rapidapi-key": self.api_key},
                params=params,
            )
        except ProviderAuthenticationError as exc:
            raise ProviderAuthenticationError("Highlightly rechazó HIGHLIGHTLY_API_KEY") from exc

    def search_players(self, name: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        result = self._get("players", params={"name": name, "limit": limit, "offset": offset})
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ProviderError("Highlightly devolvió una búsqueda de jugadores inesperada")
        return result

    def get_player_profile(self, player_id: int) -> dict[str, Any]:
        result = self._get(f"players/{player_id}")
        if isinstance(result, list):
            if len(result) == 1 and isinstance(result[0], dict):
                result = result[0]
            else:
                raise ProviderError("Highlightly devolvió un perfil de jugador inesperado")
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            result = result["data"]
        if not isinstance(result, dict):
            raise ProviderError("Highlightly devolvió un perfil de jugador inesperado")
        return result

    def get_player_statistics(self, player_id: int) -> list[dict[str, Any]]:
        result = self._get(f"players/{player_id}/statistics")
        if isinstance(result, dict):
            if isinstance(result.get("data"), list):
                result = result["data"]
            else:
                result = [result]
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ProviderError("Highlightly devolvió estadísticas de jugador inesperadas")
        return result

    def get_matches(
        self,
        *,
        league_name: str,
        match_date: str | None = None,
        country_name: str | None = None,
        season: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, object] = {
            "leagueName": league_name,
            "limit": limit,
            "offset": offset,
        }
        if match_date is not None:
            params["date"] = match_date
        if country_name is not None:
            params["countryName"] = country_name
        if season is not None:
            params["season"] = season
        result = self._get("matches", params=params)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise ProviderError("Highlightly devolvió una lista de partidos inesperada")
        return result

    def get_box_score(self, match_id: int) -> list[dict[str, Any]]:
        result = self._get(f"box-score/{match_id}")
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ProviderError("Highlightly devolvió un box score inesperado")
        return result
