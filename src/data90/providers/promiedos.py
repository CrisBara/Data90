"""Public current-season football data from Promiedos."""

import json
import re
from typing import Any

from data90.domain.exceptions import ProviderError
from data90.providers.base import HttpProvider


class PromiedosProvider(HttpProvider):
    source_code = "promiedos"
    base_url = "https://api.promiedos.com.ar"
    api_version = "1.11.7.3"
    website_base_url = "https://www.promiedos.com.ar"

    def get_league_data(self, league_id: str) -> dict[str, Any]:
        result = self.request_json(
            f"league/tables_and_fixtures/{league_id}",
            headers={"X-VER": self.api_version},
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("league"), dict)
            or not isinstance(result.get("tables_groups"), list)
            or not isinstance(result.get("players_statistics"), dict)
        ):
            raise ProviderError("Promiedos devolvió datos de liga incompletos")
        return result

    def get_league_games(self, league_id: str, filter_key: str) -> list[dict[str, Any]]:
        result = self.request_json(
            f"league/games/{league_id}/{filter_key}",
            headers={"X-VER": self.api_version},
        )
        games = result.get("games") if isinstance(result, dict) else None
        if not isinstance(games, list):
            raise ProviderError("Promiedos devolvió una lista de partidos inesperada")
        return [game for game in games if isinstance(game, dict)]

    def get_team_statistics(self, league_slug: str, league_id: str) -> dict[str, Any]:
        """Read the full, structured team-stat tables embedded in Promiedos' public page."""
        path = f"/league/{league_slug}/{league_id}/equipos"
        self.before_request()
        self.request_count += 1
        try:
            response = self.session.get(
                f"{self.website_base_url}{path}",
                headers={"User-Agent": "Data90/1.0 (source attribution: Promiedos)"},
                timeout=(5, 20),
            )
        except Exception as exc:
            raise ProviderError(
                f"No se pudo consultar la página pública de Promiedos: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(f"Promiedos respondió HTTP {response.status_code} en {path}")
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
            flags=re.DOTALL,
        )
        if match is None:
            raise ProviderError("Promiedos no publicó el payload estructurado de estadísticas")
        try:
            page = json.loads(match.group(1))
            data = page["props"]["pageProps"]["data"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("Promiedos devolvió estadísticas de equipos inválidas") from exc
        league = data.get("league") if isinstance(data, dict) else None
        tables = data.get("stats", {}).get("tables") if isinstance(data, dict) else None
        if (
            not isinstance(league, dict)
            or str(league.get("id")) != str(league_id)
            or not isinstance(data.get("teams"), list)
            or not isinstance(tables, list)
        ):
            raise ProviderError("Promiedos devolvió una página de otra liga o sin estadísticas")
        return data
