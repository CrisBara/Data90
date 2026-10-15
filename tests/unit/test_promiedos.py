import json

import requests

from football_analytics.providers.eredivisie_fantasy import EredivisieFantasyProvider
from football_analytics.providers.promiedos import PromiedosProvider
from football_analytics.services.promiedos_sync import _player_totals


class FakeResponse(requests.Response):
    def __init__(self, body: dict[str, object]) -> None:
        super().__init__()
        self.status_code = 200
        self._content = json.dumps(body).encode("utf-8")


class FakeSession:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.url = ""
        self.kwargs: dict[str, object] = {}

    def get(self, url: str, **kwargs: object) -> requests.Response:
        self.url = url
        self.kwargs = kwargs
        return self.response


def test_promiedos_provider_uses_public_current_league_endpoint() -> None:
    body = {
        "league": {"id": "h"},
        "tables_groups": [],
        "players_statistics": {"tables": []},
    }
    session = FakeSession(FakeResponse(body))
    provider = PromiedosProvider(session=session)  # type: ignore[arg-type]

    result = provider.get_league_data("h")

    assert result["league"]["id"] == "h"
    assert session.url == "https://api.promiedos.com.ar/league/tables_and_fixtures/h"
    assert session.kwargs["headers"] == {"X-VER": "1.11.7.3"}


def test_eredivisie_fantasy_provider_reads_current_bootstrap_endpoint() -> None:
    body = {"teams": [], "elements": [], "events": []}
    session = FakeSession(FakeResponse(body))
    provider = EredivisieFantasyProvider(session=session)  # type: ignore[arg-type]

    result = provider.get_bootstrap()

    assert result == body
    assert session.url == "https://fantasy.espngoal.nl/api/bootstrap-static/"


def test_promiedos_player_totals_merge_current_stat_tables() -> None:
    person = {
        "name": "Example Player",
        "team_id": "club-1",
        "position": "Delanteros",
    }
    data = {
        "players_statistics": {
            "tables": [
                {
                    "name": "Goles",
                    "rows": [
                        {
                            "entity": {"object": person},
                            "values": [{"key": "Goals", "value": "4"}],
                        }
                    ],
                },
                {
                    "name": "Asistencias",
                    "rows": [
                        {
                            "entity": {"object": person},
                            "values": [{"key": "Assists", "value": "2"}],
                        }
                    ],
                },
                {
                    "name": "Tarjetas Amarillas",
                    "rows": [
                        {
                            "entity": {"object": person},
                            "values": [{"key": "YellowCards", "value": "1"}],
                        }
                    ],
                },
            ]
        }
    }

    totals = _player_totals(data)

    assert len(totals) == 1
    values = next(iter(totals.values()))
    assert values["goals"] == 4
    assert values["assists"] == 2
    assert values["yellow_cards"] == 1
    assert values["position"] == "Delanteros"
