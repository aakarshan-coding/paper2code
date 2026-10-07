from pathlib import Path

import pytest

from paper2code.agents.scoper.fake import CANARY_DRAFT
from paper2code.agents.scoper.schemas import ScopeDraft, TestFile
from paper2code.manager.scope_files import write_scope
from paper2code.manager.stubcheck import StubCheckResult, remove_tests, run_stub_check
from paper2code.sandbox.runner import LocalTestRunner

RUNNER = LocalTestRunner(timeout_s=120)


def _scope(tmp_path, draft: ScopeDraft) -> Path:
    scope = tmp_path / "scope"
    write_scope(scope, draft)
    return scope


def _with_public(draft: ScopeDraft, *files: TestFile) -> ScopeDraft:
    return draft.model_copy(update={"public_tests": list(draft.public_tests) + list(files)})


def test_remove_tests_cuts_named_functions_with_decorators(tmp_path):
    f = tmp_path / "test_x.py"
    f.write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('s', [1, 2])\ndef test_keep(s):\n    assert s\n\n\n"
        "@pytest.mark.skip\ndef test_drop(): \n    assert True\n\n\ndef test_also_drop():\n    assert True\n\n\ndef test_last():\n    assert False\n",
        encoding="utf-8",
    )
    removed = remove_tests(f, {"test_drop", "test_also_drop", "test_missing"})
    assert sorted(removed) == ["test_also_drop", "test_drop"]
    src = f.read_text(encoding="utf-8")
    assert "test_keep" in src and "test_last" in src
    assert "test_drop" not in src and "test_also_drop" not in src and "@pytest.mark.skip" not in src
    compile(src, "test_x.py", "exec")


def test_canary_scope_passes_stub_check(tmp_path):
    scope = _scope(tmp_path, CANARY_DRAFT)
    r = run_stub_check(scope, CANARY_DRAFT.interface, RUNNER, min_seeds=3)
    assert isinstance(r, StubCheckResult)
    assert r.reject_reason is None
    assert r.removed == [] and r.errored == []
    assert sorted(r.claim_test_ids) == ["test_claim::test_method_halves_mse[0]", "test_claim::test_method_halves_mse[1]", "test_claim::test_method_halves_mse[2]"]
    assert r.public_failed == 6 and r.hidden_failed == 5


def test_trivial_test_is_removed_and_logged(tmp_path):
    draft = _with_public(CANARY_DRAFT, TestFile(path="test_planted.py", content="def test_trivial():\n    assert True\n\n\ndef test_real():\n    from canary_method import ema\n    assert ema([1.0], 0.5) == [1.0]\n"))
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason is None
    assert r.removed == ["test_planted::test_trivial"]
    src = (scope / "tests" / "public" / "test_planted.py").read_text(encoding="utf-8")
    assert "test_trivial" not in src and "test_real" in src


def test_trivial_hidden_test_is_removed_too(tmp_path):
    draft = CANARY_DRAFT.model_copy(update={"hidden_tests": CANARY_DRAFT.hidden_tests + [TestFile(path="test_h2.py", content="def test_nothing():\n    assert 1 == 1\n")]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.removed == ["test_h2::test_nothing"] and r.reject_reason is None


def test_trivial_claim_test_rejects_scope(tmp_path):
    claim = TestFile(path="test_claim.py", content="import pytest\n\n\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed):\n    assert seed >= 0\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], claim]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "trivial_claim_test"
    assert set(r.removed) == {"test_claim::test_claim[0]", "test_claim::test_claim[1]", "test_claim::test_claim[2]"}


def test_stub_check_counts_claim_seeds(tmp_path):
    claim = TestFile(path="test_claim.py", content="import pytest\nfrom canary_method import run_experiment\n\n\n@pytest.mark.parametrize('seed', [0, 1])\ndef test_claim(seed):\n    assert run_experiment(seed)['method_mse'] < 1\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], claim]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "insufficient_seeds"
    assert len(r.claim_test_ids) == 2
    unparam = TestFile(path="test_claim.py", content="from canary_method import run_experiment\n\n\ndef test_claim():\n    assert run_experiment(0)['method_mse'] < 1\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], unparam]})
    r = run_stub_check(_scope(tmp_path / "b", draft), draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "insufficient_seeds" and len(r.claim_test_ids) == 1


def test_stub_check_rejects_uncollectable_tests(tmp_path):
    bad = TestFile(path="test_broken.py", content="from canary_method import not_in_interface\n\n\ndef test_x():\n    assert not_in_interface()\n")
    draft = _with_public(CANARY_DRAFT, bad)
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "tests_do_not_collect"
    assert any("test_broken" in e for e in r.errored)


def test_stub_check_rejects_missing_package(tmp_path):
    bad = TestFile(path="test_pkg.py", content="import definitely_not_installed_pkg\n\n\ndef test_x():\n    assert definitely_not_installed_pkg\n")
    draft = CANARY_DRAFT.model_copy(update={"hidden_tests": CANARY_DRAFT.hidden_tests + [bad]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "tests_do_not_collect"


def test_stub_check_leaves_no_workspace_in_scope(tmp_path):
    scope = _scope(tmp_path, CANARY_DRAFT)
    run_stub_check(scope, CANARY_DRAFT.interface, RUNNER, min_seeds=3)
    assert sorted(p.name for p in scope.iterdir()) == ["interface.md", "spec.md", "tests"]
    assert not any(p.name == "canary_method.py" for p in scope.rglob("*"))


def test_class_based_tests_are_pruned_and_claim_classes_count(tmp_path):
    units = TestFile(path="test_units.py", content=(
        "from canary_method import ema\n\n\nclass TestEma:\n    def test_real(self):\n        assert ema([1.0], 0.5) == [1.0]\n\n"
        "    def test_trivial(self):\n        assert True\n"
    ))
    claim = TestFile(path="test_claim.py", content=(
        "import pytest\nfrom canary_method import run_experiment\n\n\nclass TestClaim:\n"
        "    @pytest.mark.parametrize('seed', [0, 1, 2])\n    def test_claim(self, seed):\n        assert run_experiment(seed)['method_mse'] < 1\n"
    ))
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [units, claim]})
    scope = _scope(tmp_path, draft)
    r = run_stub_check(scope, draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason is None, r
    assert r.removed == ["test_units.TestEma::test_trivial"]
    assert len(r.claim_test_ids) == 3
    src = (scope / "tests" / "public" / "test_units.py").read_text(encoding="utf-8")
    assert "test_trivial" not in src and "test_real" in src


def test_skipped_claim_test_rejects_scope(tmp_path):
    claim = TestFile(path="test_claim.py", content=(
        "import pytest\nfrom canary_method import run_experiment\n\n\n@pytest.mark.skip(reason='no gpu here')\n"
        "@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed):\n    assert run_experiment(seed)['method_mse'] < 1\n"
    ))
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], claim]})
    r = run_stub_check(_scope(tmp_path, draft), draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "tests_skipped"


def test_failure_unrelated_to_stubs_rejects_scope(tmp_path):
    claim = TestFile(path="test_claim.py", content=(
        "import pytest\n\n\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed):\n    assert False\n"
    ))
    draft = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], claim]})
    r = run_stub_check(_scope(tmp_path, draft), draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "tests_fail_for_other_reasons"
    assert any("test_claim" in t for t in r.wrong_failures)


def test_empty_hidden_suite_rejects_scope(tmp_path):
    draft = CANARY_DRAFT.model_copy(update={"hidden_tests": [TestFile(path="test_h.py", content="import pytest\n")]})
    r = run_stub_check(_scope(tmp_path, draft), draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "no_hidden_tests"


def test_hidden_suite_timeout_rejects_scope(tmp_path):
    hidden = TestFile(path="test_h.py", content="import time\nfrom canary_method import ema\n\n\ndef test_slow():\n    time.sleep(30)\n    assert ema([1.0], 0.5)\n")
    draft = CANARY_DRAFT.model_copy(update={"hidden_tests": [hidden]})
    r = run_stub_check(_scope(tmp_path, draft), draft.interface, LocalTestRunner(timeout_s=3), min_seeds=3)
    assert r.reject_reason == "stub_check_timeout"


def test_unprunable_passing_test_rejects_scope(tmp_path):
    dyn = TestFile(path="test_dyn.py", content="def _make():\n    def test_dyn():\n        assert True\n    return test_dyn\n\n\ntest_dyn = _make()\n")
    draft = _with_public(CANARY_DRAFT, dyn)
    r = run_stub_check(_scope(tmp_path, draft), draft.interface, RUNNER, min_seeds=3)
    assert r.reject_reason == "prune_failed"
