"""The deployed Modal app: the GPU function that runs tests, and the two images. Deploy once with
`python -m modal deploy src/paper2code/sandbox/modal_app.py`; the manager looks the function up
by name, so the unit suite never imports this module.

The GPU type and the function timeout are fixed at deploy time from environment variables, so a
different GPU means a redeploy, not a code change.
"""
from __future__ import annotations

import os
from pathlib import Path

import modal

from paper2code.config import Config, load_config
from paper2code.sandbox.remote_tests import execute_tests

# Deploy-time settings come from config.yaml in the current directory (deploy from the repo root);
# environment variables override them.
_CONFIG = load_config(Path("config.yaml")) if Path("config.yaml").exists() else Config()
GPU = os.environ.get("PAPER2CODE_GPU", _CONFIG.gpu_type)
FUNCTION_TIMEOUT_S = int(os.environ.get("PAPER2CODE_TEST_FUNCTION_TIMEOUT", str(_CONFIG.test_function_timeout_s)))

app = modal.App("paper2code")

_base = modal.Image.debian_slim(python_version="3.12").apt_install("git")

# Where the tests run: CUDA-capable torch, plus the package source so execute_tests is importable.
tests_image = (
    _base.pip_install("pytest>=8", "numpy", "scipy", "scikit-learn", "torch")
    .add_local_python_source("paper2code")
)

# Where the builder works: CPU-only torch keeps the image small; no package source, no secrets.
builder_image = (
    _base.pip_install("pytest>=8", "numpy", "scipy", "scikit-learn")
    .run_commands("pip install torch --index-url https://download.pytorch.org/whl/cpu")
)


@app.function(name="run_tests_remote", image=tests_image, gpu=GPU, timeout=FUNCTION_TIMEOUT_S)
def run_tests_remote(payload: bytes, timeout_s: int) -> dict:
    return execute_tests(payload, timeout_s)


# ---- the scheduled manager (spec 12) ------------------------------------------------------------
# Secrets live in one Modal secret, created from the shell so no value is ever written to a file:
#   python -m modal secret create paper2code OPENAI_API_KEY=$OPENAI_API_KEY CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN GITHUB_TOKEN=$GITHUB_TOKEN
# ANTHROPIC_API_KEY is deliberately absent. Set runs_repo_url in config.yaml before deploying if the
# cloud run should push the runs repository; the schedule and timeout are deploy-time environment variables.
SCHEDULE = os.environ.get("PAPER2CODE_SCHEDULE", _CONFIG.schedule_cron)
MANAGER_TIMEOUT_S = int(os.environ.get("PAPER2CODE_MANAGER_TIMEOUT", str(6 * 3600)))
SECRET_NAME = os.environ.get("PAPER2CODE_SECRET", "paper2code")

# The Agent SDK's Linux wheel bundles the claude CLI, so the builder needs no Node and no PATH entry here.
manager_image = (
    _base.pip_install_from_pyproject("pyproject.toml")
    .add_local_file("config.yaml", "/root/config.yaml")
    .add_local_python_source("paper2code")
)


@app.function(
    name="daily_run", image=manager_image, schedule=modal.Cron(SCHEDULE, timezone="UTC"),
    secrets=[modal.Secret.from_name(SECRET_NAME, required_keys=["OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])],
    timeout=MANAGER_TIMEOUT_S, cpu=2.0, memory=4096,
)
def daily_run() -> int:
    from paper2code.cli import main

    os.environ.pop("ANTHROPIC_API_KEY", None)  # the subscription token must be the only Anthropic credential
    os.environ.setdefault("PYTHONUTF8", "1")
    os.chdir("/root")
    rc = main(["daily", "--config", "/root/config.yaml"])
    if rc != 0:
        raise RuntimeError(f"daily exited {rc} (2 = preflight refused, 1 = the run or the publish failed)")  # a failed call, visible in Modal
    return rc
