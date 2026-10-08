"""Checks that cost nothing and must pass before a daily run spends anything. No secret value ever
appears in a check's detail."""
from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from paper2code.config import Config


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    skipped: bool = False


def _bundled_cli() -> Path | None:
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    name = "claude.exe" if platform.system() == "Windows" else "claude"
    path = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    return path if path.is_file() else None


def _default_modal_lookup(app_name: str, function_name: str):
    import modal

    return modal.Function.from_name(app_name, function_name).hydrate()


def _default_git_probe(url: str) -> bool:
    from paper2code.manager.runs_repo import RunsRepo

    authed = RunsRepo(Path("."), remote_url=url)._authed_url()
    proc = subprocess.run(["git", "ls-remote", authed, "HEAD"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return proc.returncode == 0


def run_preflight(
    config: Config, *, llm: str, no_gpu: bool, builder: str, publish: bool,
    modal_lookup: Callable | None = None, cli_finder: Callable[[], str | None] | None = None,
    env: Mapping[str, str] | None = None, git_probe: Callable[[str], bool] | None = None,
) -> list[Check]:
    env = os.environ if env is None else env
    checks: list[Check] = []

    if llm == "openai":
        present = bool(env.get("OPENAI_API_KEY"))
        checks.append(Check("openai_key", present, "present" if present else "OPENAI_API_KEY is not set"))
    else:
        checks.append(Check("openai_key", True, f"llm is {llm}", skipped=True))

    if builder == "agent":
        from paper2code.agents.builder.agent import find_cli

        found = (cli_finder or find_cli)()
        bundled = _bundled_cli()
        if found:
            checks.append(Check("claude_cli", True, f"found at {found}"))
        elif bundled:
            checks.append(Check("claude_cli", True, f"bundled with the Agent SDK at {bundled}"))
        else:
            checks.append(Check("claude_cli", False, "no claude executable on PATH, in CLAUDE_CODE_EXECPATH, or bundled with the SDK"))
        absent = "ANTHROPIC_API_KEY" not in env
        checks.append(Check("anthropic_key_absent", absent,
                            "absent" if absent else "ANTHROPIC_API_KEY is set; it would silently override the subscription token"))
    else:
        checks.append(Check("claude_cli", True, f"builder is {builder}", skipped=True))

    if no_gpu:
        checks.append(Check("gpu_function", True, "--no-gpu", skipped=True))
    else:
        try:
            (modal_lookup or _default_modal_lookup)(config.modal_app_name, "run_tests_remote")
            checks.append(Check("gpu_function", True, f"{config.modal_app_name}/run_tests_remote is deployed"))
        except Exception as exc:
            checks.append(Check("gpu_function", False,
                                f"{type(exc).__name__}: {str(exc)[:200]}; run `python -m modal deploy src/paper2code/sandbox/modal_app.py`"))

    ok = config.run_tests_timeout_s < config.test_function_timeout_s
    checks.append(Check("timeouts", ok,
                        f"run_tests_timeout_s={config.run_tests_timeout_s} {'<' if ok else '>='} test_function_timeout_s={config.test_function_timeout_s}"))

    if not publish:
        checks.append(Check("runs_repo", True, "publishing off", skipped=True))
    elif not config.runs_repo_url:
        checks.append(Check("runs_repo", False, "runs_repo_url is empty; set it in config.yaml or pass --no-publish"))
    else:
        if config.runs_repo_url.startswith("https://") and not env.get("GITHUB_TOKEN"):
            checks.append(Check("github_token", False, "GITHUB_TOKEN is not set; pushes to an https remote need it"))
        reachable = (git_probe or _default_git_probe)(config.runs_repo_url)
        checks.append(Check("runs_repo", reachable, "reachable" if reachable else f"cannot reach {config.runs_repo_url}"))
    return checks


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


def format_checks(checks: list[Check]) -> str:
    return "\n".join(f"{'skip' if c.skipped else 'ok  ' if c.ok else 'FAIL'} {c.name}: {c.detail}" for c in checks)
