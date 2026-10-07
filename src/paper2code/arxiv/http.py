"""A polite HTTP client for arXiv: identifies itself, spaces requests, backs off on 429/503."""
from __future__ import annotations

import time
from typing import Callable

import httpx

USER_AGENT_BASE = "paper2code/0.1"
RETRY_STATUSES = (429, 503)
BACKOFF_S = (5.0, 10.0, 20.0, 40.0, 80.0)


class ArxivUnavailable(Exception):
    """arXiv kept answering 429/503 for every attempt."""


class PoliteClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        min_interval_s: float = 3.0,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        contact: str = "",
    ) -> None:
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.min_interval_s = min_interval_s
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.user_agent = f"{USER_AGENT_BASE} (mailto:{contact})" if contact else USER_AGENT_BASE
        self._last_request_at: float | None = None

    def _wait_turn(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self.min_interval_s - elapsed
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        last_status = None
        for attempt in range(self.max_attempts):
            self._wait_turn()
            resp = self.client.get(url, params=params, headers={"User-Agent": self.user_agent}, follow_redirects=True)
            if resp.status_code not in RETRY_STATUSES:
                return resp
            last_status = resp.status_code
            if attempt < self.max_attempts - 1:
                self.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        raise ArxivUnavailable(f"{url}: {last_status} after {self.max_attempts} attempts")


def make_polite_client(ctx) -> PoliteClient:
    """The stage-facing factory. Tests set ctx.http; production builds a real client."""
    if ctx.http is not None:
        return ctx.http
    return PoliteClient(contact="sriramsattiraju@utexas.edu")
