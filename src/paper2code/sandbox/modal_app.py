"""The deployed Modal app: the GPU function that runs tests, and the two images. Deploy once with
`python -m modal deploy src/paper2code/sandbox/modal_app.py`; the manager looks the function up
by name, so the unit suite never imports this module.

The GPU type and the function timeout are fixed at deploy time from environment variables, so a
different GPU means a redeploy, not a code change.
"""
from __future__ import annotations

import os

import modal

from paper2code.sandbox.remote_tests import execute_tests

GPU = os.environ.get("PAPER2CODE_GPU", "T4")
FUNCTION_TIMEOUT_S = int(os.environ.get("PAPER2CODE_TEST_FUNCTION_TIMEOUT", "1800"))

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
