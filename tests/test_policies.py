import pytest

from paper2code.policies import get_policy
from paper2code.policies import select_v1_testability as v1


def _card(i, testability, est_usd, error=None):
    row = {"pass": 2, "arxiv_id": f"2610.{i:05d}", "title": f"T{i}", "testability": testability, "est_usd": est_usd, "difficulty": "easy"}
    if error:
        row = {"pass": 2, "arxiv_id": f"2610.{i:05d}", "title": f"T{i}", "error": error}
    return row


def test_registry_resolves_v1():
    mod = get_policy("select_v1_testability")
    assert mod.NAME == "select_v1_testability" and mod.VERSION == "1"
    with pytest.raises(KeyError):
        get_policy("select_v9_nope")


def test_v1_filters_by_budget_then_sorts_testability_desc_cost_asc():
    cards = [_card(1, 5, 12.0), _card(2, 4, 3.0), _card(3, 5, 2.0), _card(4, 4, 1.0), _card(5, 3, 0.5), _card(6, 5, 9.0)]
    out = v1.select(cards, limit_usd=10.0, k=3)
    assert [c["arxiv_id"] for c in out] == ["2610.00003", "2610.00006", "2610.00004"]


def test_v1_skips_error_rows_and_handles_empty():
    assert v1.select([], 10.0, 3) == []
    assert v1.select([_card(1, 5, 1.0, error="fulltext_unavailable")], 10.0, 3) == []
    assert v1.select([_card(1, 5, 50.0)], 10.0, 3) == []
