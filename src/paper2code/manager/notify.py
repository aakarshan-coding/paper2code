"""Optional notification at the end of a daily run: one JSON POST. Off unless notify_url is set; never raises."""
from __future__ import annotations

import httpx


def notify(url: str, payload: dict, post=None) -> bool:
    if not url:
        return False
    post = post or (lambda u, p: httpx.post(u, json=p, timeout=10.0))
    try:
        resp = post(url, payload)
        return 200 <= getattr(resp, "status_code", 0) < 300
    except Exception:
        return False
