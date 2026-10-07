import ast

import pytest

from paper2code.agents.scoper.schemas import (
    CLAIM_TEST_FILE,
    ClassSpec,
    FunctionSpec,
    InterfaceSpec,
    MethodSpec,
    ScopeDraft,
    TestFile,
    validate_draft,
)
from paper2code.manager.scope_files import render_interface_md, render_stubs, write_scope


def _draft(**over):
    base = dict(
        spec_md="# Scope\n\nSmooth a noisy sine.\n",
        interface=InterfaceSpec(
            module="canary_method",
            functions=[
                FunctionSpec(signature="def ema(xs: list[float], alpha: float) -> list[float]", doc="EMA smoothing."),
                FunctionSpec(signature="def run_experiment(seed: int, n: int = 500) -> dict[str, float]", doc="One trial."),
            ],
            classes=[ClassSpec(name="Model", doc="A model.", methods=[MethodSpec(signature="def fit(self, xs: list[float]) -> None", doc="Fit.")])],
        ),
        public_tests=[
            TestFile(path="test_units.py", content="from canary_method import ema\ndef test_ema(): assert ema([1.0], 0.5) == [1.0]\n"),
            TestFile(path=CLAIM_TEST_FILE, content="import pytest\nfrom canary_method import run_experiment\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed): assert run_experiment(seed)['method_mse'] < 0.5\n"),
        ],
        hidden_tests=[TestFile(path="test_claim_hidden.py", content="from canary_method import run_experiment\ndef test_claim_hidden(): assert run_experiment(7)['method_mse'] < 0.5\n")],
        seeds=[0, 1, 2],
        est_gpu_hours=0.01,
        est_usd=0.01,
        notes="",
    )
    base.update(over)
    return ScopeDraft(**base)


def test_valid_draft_has_no_problems():
    assert validate_draft(_draft()) == []


def test_validate_draft_rejects_unsafe_paths():
    for bad in ("../test_x.py", "sub/test_x.py", "/tmp/test_x.py", "notatest.py", "test_x.txt", "test-x.py"):
        problems = validate_draft(_draft(hidden_tests=[TestFile(path=bad, content="def test_h(): assert False\n")]))
        assert any(bad in p for p in problems), bad


def test_validate_draft_requires_claim_file_and_hidden_tests():
    assert any("test_claim.py" in p for p in validate_draft(_draft(public_tests=[TestFile(path="test_units.py", content="def test_a(): assert False\n")])))
    assert any("hidden" in p for p in validate_draft(_draft(hidden_tests=[])))


def test_validate_draft_rejects_bad_python_and_signatures():
    assert any("test_units.py" in p for p in validate_draft(_draft(public_tests=[TestFile(path="test_units.py", content="def test_a(:\n"), _draft().public_tests[1]])))
    bad_iface = InterfaceSpec(module="m", functions=[FunctionSpec(signature="ema(xs)", doc="")], classes=[])
    assert any("signature" in p for p in validate_draft(_draft(interface=bad_iface)))
    assert any("module" in p for p in validate_draft(_draft(interface=InterfaceSpec(module="not valid", functions=[], classes=[]))))


def test_validate_draft_rejects_duplicates_and_bad_seeds():
    dup = [TestFile(path="test_claim.py", content="def test_a(): assert False\n")] * 2
    assert any("duplicate" in p for p in validate_draft(_draft(public_tests=dup)))
    assert any("seeds" in p for p in validate_draft(_draft(seeds=[])))
    assert any("seeds" in p for p in validate_draft(_draft(seeds=[1, 1])))
    assert any("est_usd" in p for p in validate_draft(_draft(est_usd=-1.0)))


def test_render_stubs_raise_not_implemented_and_import_cleanly(tmp_path):
    src = render_stubs(_draft().interface)
    ast.parse(src)
    (tmp_path / "canary_method.py").write_text(src, encoding="utf-8")
    import importlib.util

    spec = importlib.util.spec_from_file_location("canary_method", tmp_path / "canary_method.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(NotImplementedError):
        mod.ema([1.0], 0.5)
    with pytest.raises(NotImplementedError):
        mod.Model().fit([1.0])
    assert mod.Model(1, 2, k=3) is not None  # constructor stub accepts anything


def test_render_interface_md_lists_everything():
    md = render_interface_md(_draft().interface)
    assert "canary_method" in md and "def ema(xs: list[float], alpha: float) -> list[float]" in md
    assert "class Model" in md and "def fit(self, xs: list[float]) -> None" in md and "EMA smoothing." in md


def test_write_scope_lays_out_files(tmp_path):
    scope = tmp_path / "scope"
    write_scope(scope, _draft())
    assert (scope / "spec.md").read_text(encoding="utf-8").startswith("# Scope")
    assert "def ema" in (scope / "interface.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in (scope / "tests" / "public").iterdir()) == ["test_claim.py", "test_units.py"]
    assert [p.name for p in (scope / "tests" / "hidden").iterdir()] == ["test_claim_hidden.py"]


def test_write_scope_refuses_invalid_draft(tmp_path):
    with pytest.raises(ValueError, match="hidden"):
        write_scope(tmp_path / "scope", _draft(hidden_tests=[]))
    assert not (tmp_path / "scope").exists()


def test_signatures_with_trailing_colon_are_accepted_and_normalised(tmp_path):
    iface = InterfaceSpec(
        module="m",
        functions=[FunctionSpec(signature="def set_seed(seed: int) -> None:", doc="Seed.")],
        classes=[ClassSpec(name="TinyForecaster", doc="", methods=[MethodSpec(signature="def forward(self, x: int) -> int: ", doc="")])],
    )
    assert validate_draft(_draft(interface=iface)) == []
    stubs = render_stubs(iface)
    assert "def set_seed(seed: int) -> None:\n" in stubs and "::" not in stubs
    md = render_interface_md(iface)
    assert "def set_seed(seed: int) -> None:\n" in md and "::" not in md
    ast.parse(stubs)


def test_stubs_with_third_party_annotations_import_without_those_packages(tmp_path):
    iface = InterfaceSpec(
        module="m",
        functions=[FunctionSpec(signature="def make_windows(series: np.ndarray, device: str | None = None) -> tuple[torch.Tensor, torch.Tensor]", doc="")],
        classes=[],
    )
    assert validate_draft(_draft(interface=iface)) == []
    (tmp_path / "m.py").write_text(render_stubs(iface), encoding="utf-8")
    import importlib.util

    spec = importlib.util.spec_from_file_location("m", tmp_path / "m.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # must not raise NameError on np/torch
    with pytest.raises(NotImplementedError):
        mod.make_windows(None)
