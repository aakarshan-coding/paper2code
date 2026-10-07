"""v1: highest testability under budget, cheapest first among ties."""
from __future__ import annotations

NAME = "select_v1_testability"
VERSION = "1"


def select(cards: list[dict], limit_usd: float, k: int) -> list[dict]:
    scored = [c for c in cards if "testability" in c and "error" not in c]
    affordable = [c for c in scored if float(c.get("est_usd", float("inf"))) <= limit_usd]
    ranked = sorted(affordable, key=lambda c: (-int(c["testability"]), float(c["est_usd"])))
    return ranked[:k]
