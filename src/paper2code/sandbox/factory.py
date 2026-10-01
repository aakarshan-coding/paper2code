from __future__ import annotations

from paper2code.manager.graph import RunContext
from paper2code.sandbox.runner import LocalTestRunner, TestRunner


def make_runner(ctx: RunContext) -> TestRunner:
    if ctx.no_gpu:
        return LocalTestRunner(timeout_s=ctx.config.run_tests_timeout_s)
    raise NotImplementedError("Modal GPU runner lands in build step 4; pass --no-gpu")
