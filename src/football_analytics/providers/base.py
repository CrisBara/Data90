from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from tenacity.wait import wait_base

from football_analytics.domain.exceptions import ProviderAuthenticationError, ProviderError

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ProviderRetryWait(wait_base):
    def __init__(self) -> None:
        self.exponential = wait_exponential(multiplier=2, min=2, max=120)

    def __call__(self, retry_state: Any) -> float:
        error = retry_state.outcome.exception() if retry_state.outcome else None
        retry_after = getattr(error, "retry_after", None)
        if isinstance(retry_after, int | float):
            return max(0, float(retry_after))
        if isinstance(retry_after, str):
            if retry_after.strip().isdigit():
                return float(retry_after.strip())
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                return max(0, (retry_at - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
        return self.exponential(retry_state)


def _retryable_provider_error(error: BaseException) -> bool:
    if isinstance(error, requests.Timeout | requests.ConnectionError):
        return True
    return (
        isinstance(error, ProviderError)
        and getattr(error, "status_code", None) in RETRYABLE_STATUS_CODES
    )


class HttpProvider:
    source_code = ""
    base_url = ""

    def __init__(self, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.request_count = 0

    def before_request(self) -> None:
        """Hook for provider-specific rate limiting."""

    @retry(
        retry=retry_if_exception(_retryable_provider_error),
        stop=stop_after_attempt(10),
        wait=ProviderRetryWait(),
        reraise=True,
    )
    def request_json(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Any:
        self.before_request()
        self.request_count += 1
        try:
            response = self.session.get(
                f"{self.base_url.rstrip('/')}/{path.lstrip('/')}",
                headers=headers,
                params=params,
                timeout=(5, 20),
                allow_redirects=False,
            )
        except (requests.Timeout, requests.ConnectionError):
            raise
        if response.status_code in (401, 403):
            raise ProviderAuthenticationError(f"{self.source_code} rechazó la autenticación")
        if response.status_code >= 400:
            error = ProviderError(
                f"{self.source_code} respondió HTTP {response.status_code} en {path}"
            )
            error.status_code = response.status_code
            if response.status_code == 429:
                error.retry_after = response.headers.get("Retry-After")
            raise error
        try:
            return response.json()
        except requests.JSONDecodeError as exc:
            raise ProviderError(f"{self.source_code} devolvió JSON inválido") from exc
