"""Current Eredivisie roster and season statistics from the official fantasy API."""

from typing import Any

from data90.domain.exceptions import ProviderError
from data90.providers.base import HttpProvider


class EredivisieFantasyProvider(HttpProvider):
    source_code = "fantasy-eredivisie"
    base_url = "https://fantasy.espngoal.nl/api"

    def get_bootstrap(self) -> dict[str, Any]:
        result = self.request_json("bootstrap-static/")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("teams"), list)
            or not isinstance(result.get("elements"), list)
            or not isinstance(result.get("events"), list)
        ):
            raise ProviderError("Fantasy Eredivisie devolvió datos de temporada incompletos")
        return result
