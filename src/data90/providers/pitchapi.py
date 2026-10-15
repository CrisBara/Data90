"""Client for PitchAPI's free football statistics API."""

from typing import Any

from data90.config.settings import get_settings
from data90.domain.exceptions import ProviderAuthenticationError, ProviderError
from data90.providers.base import HttpProvider


class PitchAPIProvider(HttpProvider):
    source_code = "pitchapi"
    base_url = "https://api.pitchapi.dev/v1"

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = (
            api_key if api_key is not None else get_settings().pitchapi_api_key.get_secret_value()
        )

    def get(self, path: str, *, params: dict[str, object] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderAuthenticationError("Falta PITCHAPI_API_KEY en el entorno")
        result = self.request_json(
            path,
            headers={"X-API-KEY": self.api_key},
            params=params,
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict | list):
            raise ProviderError(f"{self.source_code} devolvió una estructura inesperada")
        return result["data"]

    def get_leagues(self) -> list[dict[str, Any]]:
        data = self.get("leagues")
        leagues = data.get("leagues") if isinstance(data, dict) else data
        if not isinstance(leagues, list):
            raise ProviderError("PitchAPI no devolvió su catálogo de ligas")
        return [row for row in leagues if isinstance(row, dict)]

    def get_league_matches(self, league_id: str, season: str) -> list[dict[str, Any]]:
        data = self.get(
            f"leagues/{league_id}/matches",
            params={"season": season, "status": "played"},
        )
        matches = data.get("matches") if isinstance(data, dict) else None
        if not isinstance(matches, list):
            raise ProviderError("PitchAPI no devolvió partidos de liga")
        return [row for row in matches if isinstance(row, dict)]

    def get_match_stats(self, match_id: str) -> dict[str, Any]:
        data = self.get(f"matches/{match_id}/stats")
        if not isinstance(data, dict):
            raise ProviderError("PitchAPI no devolvió estadísticas de equipo")
        return data

    def get_match_players(self, match_id: str) -> list[dict[str, Any]]:
        data = self.get(f"matches/{match_id}/players")
        if not isinstance(data, list):
            raise ProviderError("PitchAPI no devolvió estadísticas de jugadores")
        return [row for row in data if isinstance(row, dict)]

    def get_match_lineups(self, match_id: str) -> dict[str, Any]:
        data = self.get(f"matches/{match_id}/lineups")
        if not isinstance(data, dict):
            raise ProviderError("PitchAPI no devolvió alineaciones")
        return data

    def get_match_shots(self, match_id: str) -> list[dict[str, Any]]:
        data = self.get(f"matches/{match_id}/shots")
        if not isinstance(data, dict) or not isinstance(data.get("periods"), list):
            raise ProviderError("PitchAPI no devolvió los remates del partido")
        shots = [
            shot
            for period in data["periods"]
            if isinstance(period, dict)
            for shot in period.get("shots", [])
            if isinstance(shot, dict)
        ]
        return shots

    def get_match_events(self, match_id: str) -> list[dict[str, Any]]:
        data = self.get(f"matches/{match_id}/events")
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
            raise ProviderError("PitchAPI no devolvió los eventos del partido")
        return [row for row in events if isinstance(row, dict)]

    def get_player(self, player_id: str) -> dict[str, Any]:
        data = self.get(f"players/{player_id}")
        if not isinstance(data, dict):
            raise ProviderError("PitchAPI no devolvió el perfil del jugador")
        return data
