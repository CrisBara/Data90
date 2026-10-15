import json

import pytest
import requests

from football_analytics.domain.exceptions import ProviderAuthenticationError, ProviderError
from football_analytics.providers.highlightly import HighlightlyProvider


class FakeResponse(requests.Response):
    def __init__(self, body: object, status: int = 200) -> None:
        super().__init__()
        self.status_code = status
        self._content = json.dumps(body).encode("utf-8")


class FakeSession:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def get(self, *_args: object, **kwargs: object) -> requests.Response:
        self.calls.append(kwargs)
        return self.response


def test_player_lookup_uses_highlightly_header_and_query() -> None:
    session = FakeSession(FakeResponse({"data": [{"id": 9, "name": "Player"}]}))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    result = provider.search_players("Player", limit=5, offset=10)

    assert result["data"][0]["id"] == 9
    assert session.calls[0]["headers"] == {"x-rapidapi-key": "secret-key"}
    assert session.calls[0]["params"] == {"name": "Player", "limit": 5, "offset": 10}


def test_player_statistics_accept_documented_array_response() -> None:
    session = FakeSession(FakeResponse([{"id": 9, "perCompetition": [{"season": "2026"}]}]))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    result = provider.get_player_statistics(9)

    assert result == [{"id": 9, "perCompetition": [{"season": "2026"}]}]
    assert session.calls[0]["headers"] == {"x-rapidapi-key": "secret-key"}


def test_player_profile_accepts_documented_array_response() -> None:
    session = FakeSession(FakeResponse([{"id": 9, "profile": {"foot": "right"}}]))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    result = provider.get_player_profile(9)

    assert result["profile"]["foot"] == "right"


def test_authentication_error_does_not_expose_key() -> None:
    session = FakeSession(FakeResponse({"error": "unauthorized"}, status=401))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    with pytest.raises(ProviderAuthenticationError) as error:
        provider.get_player_profile(9)

    assert "secret-key" not in str(error.value)


def test_player_search_rejects_unexpected_response_shape() -> None:
    session = FakeSession(FakeResponse({"results": []}))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="búsqueda de jugadores inesperada"):
        provider.search_players("Player")


def test_box_score_request_uses_match_id_and_returns_team_rows() -> None:
    session = FakeSession(FakeResponse([{"team": {"name": "Inter"}, "players": [{"id": 9}]}]))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    result = provider.get_box_score(123)

    assert result[0]["players"][0]["id"] == 9
    assert session.calls[0]["headers"] == {"x-rapidapi-key": "secret-key"}


def test_match_listing_supports_season_pagination_without_a_date_filter() -> None:
    session = FakeSession(FakeResponse({"data": [{"id": 123}]}))
    provider = HighlightlyProvider("secret-key", session=session)  # type: ignore[arg-type]

    result = provider.get_matches(
        league_name="Serie A",
        country_name="Italy",
        season=2026,
        limit=100,
        offset=100,
    )

    assert result["data"][0]["id"] == 123
    assert session.calls[0]["params"] == {
        "leagueName": "Serie A",
        "countryName": "Italy",
        "limit": 100,
        "offset": 100,
        "season": 2026,
    }
