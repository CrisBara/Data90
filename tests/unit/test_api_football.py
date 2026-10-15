import json
from datetime import date

import pytest
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from data90.database.base import Base
from data90.database.models import AppSetting
from data90.domain.exceptions import ProviderAuthenticationError
from data90.providers.api_football import ApiFootballProvider
from data90.services.api_football_player_sync import (
    QUOTA_SETTING,
    DailyQuotaReached,
    _player_statistics,
    _reserve_daily_request,
    _selected_league_indices,
)


class FakeResponse(requests.Response):
    def __init__(self, body: dict[str, object]) -> None:
        super().__init__()
        self.status_code = 200
        self._content = json.dumps(body).encode("utf-8")


class FakeSession:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.kwargs: dict[str, object] = {}

    def get(self, *_args: object, **kwargs: object) -> requests.Response:
        self.kwargs = kwargs
        return self.response


def test_api_football_player_page_uses_api_sports_auth_header() -> None:
    session = FakeSession(
        FakeResponse({"errors": [], "response": [], "paging": {"current": 1, "total": 1}})
    )
    provider = ApiFootballProvider("test-key", session=session)  # type: ignore[arg-type]

    payload = provider.get_player_page(39, 2026, 1)

    assert payload["response"] == []
    assert session.kwargs["headers"] == {"x-apisports-key": "test-key"}
    assert session.kwargs["params"] == {"league": 39, "season": 2026, "page": 1}


def test_api_football_rejects_invalid_key_error_payload() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "errors": {"token": "invalid API key"},
                "response": [],
                "paging": {"current": 1, "total": 1},
            }
        )
    )
    provider = ApiFootballProvider("invalid", session=session)  # type: ignore[arg-type]

    with pytest.raises(ProviderAuthenticationError):
        provider.get_player_page(39, 2026, 1)


def test_player_statistics_aggregate_team_rows_and_preserve_unknowns() -> None:
    result = _player_statistics(
        [
            {"games": {"appearences": 12, "minutes": 900}, "goals": {"total": 4}},
            {"games": {"appearences": 3, "minutes": 120}, "goals": {"total": 1}},
        ]
    )

    assert result["appearances"] == 15
    assert result["minutes_played"] == 1020
    assert result["goals"] == 5
    assert result["assists"] is None


def test_api_football_daily_budget_is_persisted_and_resets_by_date() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    today = date(2026, 10, 6)

    for _ in range(90):
        _reserve_daily_request(factory, today)
    with pytest.raises(DailyQuotaReached):
        _reserve_daily_request(factory, today)
    _reserve_daily_request(factory, date(2026, 10, 7))

    with factory() as session:
        setting = session.scalar(select(AppSetting).where(AppSetting.key == QUOTA_SETTING))
        assert setting is not None
        assert setting.value == {"date": "2026-10-07", "used": 1}
    engine.dispose()


def test_api_football_selected_competitions_preserve_global_cursor_order() -> None:
    indices, start = _selected_league_indices(("UCL", "UEL"), next_competition=5)

    assert indices == [6, 7]
    assert start == 0

    all_indices, all_start = _selected_league_indices(None, next_competition=5)
    assert all_indices == list(range(8))
    assert all_start == 5
