import json

import requests

from football_analytics.domain.exceptions import ProviderAuthenticationError
from football_analytics.providers.base import HttpProvider


class FixtureResponse(requests.Response):
    def __init__(self, status: int, *, body: object = None, headers: dict[str, str] | None = None):
        super().__init__()
        self.status_code = status
        self.headers.update(headers or {})
        self._content = json.dumps(body).encode("utf-8")


class FixtureSession:
    def __init__(self, responses: list[requests.Response | Exception]):
        self.responses = iter(responses)
        self.calls = 0

    def get(self, *args: object, **kwargs: object) -> requests.Response:
        self.calls += 1
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class FixtureProvider(HttpProvider):
    source_code = "fixture"
    base_url = "https://example.invalid"


def test_429_retries_using_retry_after(monkeypatch) -> None:
    session = FixtureSession(
        [
            FixtureResponse(429, headers={"Retry-After": "0"}),
            FixtureResponse(200, body={"ok": True}),
        ]
    )
    provider = FixtureProvider(session=session)  # type: ignore[arg-type]
    result = provider.request_json("sample")
    assert result == {"ok": True}
    assert session.calls == 2


def test_timeout_is_retried_three_times(monkeypatch) -> None:
    monkeypatch.setattr(HttpProvider.request_json.retry, "wait", lambda _state: 0)
    session = FixtureSession(
        [requests.Timeout(), requests.Timeout(), FixtureResponse(200, body={"ok": True})]
    )
    provider = FixtureProvider(session=session)  # type: ignore[arg-type]
    assert provider.request_json("sample") == {"ok": True}
    assert session.calls == 3


def test_invalid_token_is_not_retried() -> None:
    session = FixtureSession([FixtureResponse(401)])
    provider = FixtureProvider(session=session)  # type: ignore[arg-type]
    try:
        provider.request_json("sample")
    except ProviderAuthenticationError:
        pass
    else:
        raise AssertionError("Se esperaba error de autenticación")
    assert session.calls == 1
