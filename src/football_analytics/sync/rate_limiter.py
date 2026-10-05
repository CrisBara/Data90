from collections import deque
from threading import Lock
from time import monotonic, sleep


class SlidingWindowRateLimiter:
    """Process-local limiter for a fixed number of requests in a rolling window."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("El límite y la ventana deben ser positivos")
        self.limit = limit
        self.window_seconds = window_seconds
        self._calls: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.limit:
                    self._calls.append(now)
                    return
                delay = self.window_seconds - (now - self._calls[0])
            sleep(max(delay, 0.01))
