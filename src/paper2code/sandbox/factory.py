"""Which TestRunner a run uses: local pytest with --no-gpu, otherwise the deployed Modal GPU function."""
from __future__ import annotations

from paper2code.manager.graph import RunContext
from paper2code.sandbox.runner import LocalTestRunner, TestRunner


def make_runner(ctx: RunContext) -> TestRunner:
    if ctx.no_gpu:
        return LocalTestRunner(timeout_s=ctx.config.run_tests_timeout_s)
    from paper2code.sandbox.modal_runner import ModalTestRunner

    cfg = ctx.config
    return ModalTestRunner(timeout_s=cfg.run_tests_timeout_s, max_payload_bytes=cfg.payload_max_mb * 2**20, app_name=cfg.modal_app_name)
