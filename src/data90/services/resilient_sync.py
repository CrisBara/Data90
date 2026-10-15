"""Retry a failed source phase without rerunning the rest of the sync pipeline."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

import requests

from data90.domain.exceptions import ProviderAuthenticationError, ProviderError

T = TypeVar("T")
MAX_PHASE_RETRY_SECONDS = 3 * 60 * 60
MAX_RETRY_DELAY_SECONDS = 15 * 60
_SYNC_RETRY_STARTED = time.monotonic()


def _error_count(result: object) -> int:
    if isinstance(result, dict):
        return int(result.get("error_count", 0) or 0)
    return int(getattr(result, "error_count", 0) or 0)


def _message(result: object) -> str:
    if isinstance(result, dict):
        return str(result.get("message", ""))
    return str(getattr(result, "message", ""))


def _is_authentication_failure(result: object) -> bool:
    message = _message(result).casefold()
    return any(
        marker in message
        for marker in (
            "rechazó la autenticación",
            "rechazo la autenticacion",
            "invalid api key",
            "invalid token",
            "rechazó api-football_token",
            "rechazó api_football_token",
            "unauthorized",
            "forbidden",
            "falta la clave",
        )
    )


def _retry_after(error: BaseException) -> float | None:
    value = getattr(error, "retry_after", None)
    if isinstance(value, int | float):
        return max(0.0, float(value))
    if isinstance(value, str) and value.strip():
        if value.strip().isdigit():
            return float(value.strip())
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            return max(0.0, (target - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def run_resilient_phase(
    name: str,
    operation: Callable[[], T],
    *,
    log: Callable[[str], None] = print,
    error_count: Callable[[object], int] = _error_count,
    max_runtime_seconds: int = MAX_PHASE_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run one idempotent sync phase until clean or its retry window expires.

    Provider requests already honor HTTP Retry-After at the transport layer.
    This outer retry handles sources that report an exhausted transient request
    as an error count, while keeping successfully completed phases untouched.
    """
    started = time.monotonic()
    attempt = 0
    delay = 30.0
    last_result: T | None = None

    while True:
        attempt += 1
        try:
            result = operation()
        except ProviderAuthenticationError:
            raise
        except ProviderError as error:
            if getattr(error, "status_code", None) not in {429, 500, 502, 503, 504}:
                raise
            last_result = None
            wait_seconds = _retry_after(error) or delay
            errors = 1
        except (requests.Timeout, requests.ConnectionError):
            last_result = None
            wait_seconds = delay
            errors = 1
        else:
            last_result = result
            errors = int(error_count(result) or 0)
            if errors == 0 or _is_authentication_failure(result):
                if errors:
                    log(f"{name}: no se reintenta; la fuente rechazó la credencial.")
                return result
            wait_seconds = delay

        elapsed = time.monotonic() - started
        remaining = min(
            max_runtime_seconds - elapsed,
            MAX_PHASE_RETRY_SECONDS - (time.monotonic() - _SYNC_RETRY_STARTED),
        )
        if remaining <= 0:
            log(
                f"{name}: se agotó la ventana de reintentos de "
                f"{max_runtime_seconds // 3600} h; quedan {errors} error(es)."
            )
            if last_result is not None:
                return last_result
            raise ProviderError(f"{name}: la fuente sigue fallando tras {attempt} intentos")

        wait_seconds = min(max(1.0, wait_seconds), MAX_RETRY_DELAY_SECONDS, remaining)
        log(
            f"{name}: {errors} error(es) en el intento {attempt}; "
            f"reintento en {int(wait_seconds)} s."
        )
        sleep(wait_seconds)
        delay = min(delay * 2, MAX_RETRY_DELAY_SECONDS)
