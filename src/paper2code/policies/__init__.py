"""Selection policies: plain functions over pass-two scorecard rows. Spec section 7."""
from __future__ import annotations

import importlib
from types import ModuleType

KNOWN = ("select_v1_testability",)


def get_policy(name: str) -> ModuleType:
    if name not in KNOWN:
        raise KeyError(f"unknown policy {name!r}; known: {', '.join(KNOWN)}")
    return importlib.import_module(f"paper2code.policies.{name}")
