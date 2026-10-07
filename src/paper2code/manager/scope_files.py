"""Turn a ScopeDraft into the files under scope/: spec.md, interface.md, tests, and the stub module."""
from __future__ import annotations

from pathlib import Path

from paper2code.agents.scoper.schemas import InterfaceSpec, ScopeDraft, validate_draft


def render_interface_md(interface: InterfaceSpec) -> str:
    lines = [
        "# Interface",
        "",
        f"Module `{interface.module}` (file `{interface.module}.py` at the workspace root). The tests import from it.",
        "",
        "```python",
    ]
    for fn in interface.functions:
        lines += [f"{fn.signature}:", f'    """{fn.doc}"""', ""]
    for cls in interface.classes:
        lines += [f"class {cls.name}:", f'    """{cls.doc}"""']
        for m in cls.methods:
            lines += [f"    {m.signature}:", f'        """{m.doc}"""', ""]
        lines.append("")
    lines.append("```")
    return "\n".join(lines) + "\n"


def render_stubs(interface: InterfaceSpec) -> str:
    """A module where every function and method raises NotImplementedError. Used by the stub check."""
    lines = [f'"""Stub of {interface.module}: every call raises NotImplementedError."""', ""]
    for fn in interface.functions:
        lines += [f"{fn.signature}:", "    raise NotImplementedError", "", ""]
    for cls in interface.classes:
        lines.append(f"class {cls.name}:")
        if not any(m.signature.startswith("def __init__") for m in cls.methods):
            lines += ["    def __init__(self, *args, **kwargs):", "        pass", ""]
        for m in cls.methods:
            lines += [f"    {m.signature}:", "        raise NotImplementedError", ""]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_scope(scope_dir: Path, draft: ScopeDraft) -> None:
    problems = validate_draft(draft)
    if problems:
        raise ValueError("; ".join(problems))
    (scope_dir / "tests" / "public").mkdir(parents=True, exist_ok=True)
    (scope_dir / "tests" / "hidden").mkdir(parents=True, exist_ok=True)
    (scope_dir / "spec.md").write_text(draft.spec_md, encoding="utf-8")
    (scope_dir / "interface.md").write_text(render_interface_md(draft.interface), encoding="utf-8")
    for tf in draft.public_tests:
        (scope_dir / "tests" / "public" / tf.path).write_text(tf.content, encoding="utf-8")
    for tf in draft.hidden_tests:
        (scope_dir / "tests" / "hidden" / tf.path).write_text(tf.content, encoding="utf-8")
