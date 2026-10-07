"""Structured output of the scoper: the whole assignment as data. The manager writes the files."""
from __future__ import annotations

import ast
import re
from typing import ClassVar

from pydantic import BaseModel

CLAIM_TEST_FILE = "test_claim.py"
_TEST_PATH = re.compile(r"^test_[A-Za-z0-9_]+\.py$")


class FunctionSpec(BaseModel):
    signature: str  # e.g. "def ema(xs: list[float], alpha: float) -> list[float]"
    doc: str


class MethodSpec(BaseModel):
    signature: str  # e.g. "def fit(self, xs: list[float]) -> None"
    doc: str


class ClassSpec(BaseModel):
    name: str
    doc: str
    methods: list[MethodSpec]


class InterfaceSpec(BaseModel):
    module: str  # importable module name; the file is <module>.py at the workspace root
    functions: list[FunctionSpec]
    classes: list[ClassSpec]


class TestFile(BaseModel):
    __test__: ClassVar[bool] = False  # not a pytest test class despite the name

    path: str  # flat file name, test_*.py
    content: str


class ScopeDraft(BaseModel):
    spec_md: str
    interface: InterfaceSpec
    public_tests: list[TestFile]
    hidden_tests: list[TestFile]
    seeds: list[int]
    est_gpu_hours: float
    est_usd: float
    notes: str


def _signature_ok(signature: str, indent: str = "") -> bool:
    source = (
        f"{signature}:\n    pass\n"
        if not indent
        else f"class _C:\n{indent}{signature}:\n{indent}    pass\n"
    )
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    node = tree.body[0] if not indent else tree.body[0].body[0]
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _test_files_ok(files: list[TestFile], group: str, problems: list[str]) -> None:
    seen: set[str] = set()
    for tf in files:
        if not _TEST_PATH.match(tf.path):
            problems.append(f"{group} test path {tf.path!r} must be a flat test_*.py file name")
            continue
        if tf.path in seen:
            problems.append(f"duplicate {group} test path {tf.path!r}")
        seen.add(tf.path)
        try:
            ast.parse(tf.content)
        except SyntaxError as exc:
            problems.append(f"{group} test file {tf.path} is not valid Python: {exc.msg} (line {exc.lineno})")


def validate_draft(draft: ScopeDraft) -> list[str]:
    """Problems with a draft. Empty means the manager may write it. Nothing is written here."""
    problems: list[str] = []
    if not draft.interface.module.isidentifier():
        problems.append(f"module name {draft.interface.module!r} is not a valid identifier")
    for fn in draft.interface.functions:
        if not fn.signature.startswith("def ") or not _signature_ok(fn.signature):
            problems.append(f"function signature {fn.signature!r} does not parse as a def line")
    for cls in draft.interface.classes:
        if not cls.name.isidentifier():
            problems.append(f"class name {cls.name!r} is not a valid identifier")
        for m in cls.methods:
            if not m.signature.startswith("def ") or not _signature_ok(m.signature, indent="    "):
                problems.append(f"method signature {m.signature!r} in class {cls.name} does not parse as a def line")
    _test_files_ok(draft.public_tests, "public", problems)
    _test_files_ok(draft.hidden_tests, "hidden", problems)
    if not any(tf.path == CLAIM_TEST_FILE for tf in draft.public_tests):
        problems.append(f"public tests must include {CLAIM_TEST_FILE}")
    if not draft.hidden_tests:
        problems.append("hidden tests must not be empty")
    if not draft.seeds or len(set(draft.seeds)) != len(draft.seeds):
        problems.append("seeds must be a non-empty list of distinct integers")
    if draft.est_usd < 0 or draft.est_gpu_hours < 0:
        problems.append("est_usd and est_gpu_hours must be non-negative")
    return problems
