"""paper2code command line. Local mode only in this build step."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from paper2code.config import Config, load_config
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.local import init_run
from paper2code.manager.record import Paper

STAGE_COMMANDS = ("build", "inspect", "report")


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, type=Path, help="run directory, e.g. runs/2026-09-30")
    p.add_argument("--no-gpu", action="store_true", help="use a local subprocess instead of a Modal sandbox")
    p.add_argument("--builder", choices=["stub", "agent"], default="stub")
    p.add_argument("--reference", type=Path, help="reference implementation dir for --builder stub")
    p.add_argument("--config", type=Path, default=Path("config.yaml"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper2code")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-run", help="seed a run at the scope stage from a pre-written scope directory")
    init.add_argument("--scope", required=True, type=Path)
    init.add_argument("--paper-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--url", default="")
    init.add_argument("--runs-root", type=Path)
    init.add_argument("--config", type=Path, default=Path("config.yaml"))
    init.add_argument("--date", type=date.fromisoformat, default=None)

    run = sub.add_parser("run", help="run the pipeline from the run's current stage to the end")
    _add_run_args(run)
    for name in STAGE_COMMANDS:
        _add_run_args(sub.add_parser(name, help=f"run only the {name} stage"))
    return parser


def _load_config(path: Path) -> Config:
    return load_config(path) if path.exists() else Config()


def _context(parser: argparse.ArgumentParser, args: argparse.Namespace) -> RunContext:
    if not args.no_gpu:
        parser.error("the Modal GPU sandbox lands in build step 4; pass --no-gpu")
    if args.builder == "stub" and args.reference is None:
        parser.error("--builder stub requires --reference DIR")
    return RunContext(
        config=_load_config(args.config),
        no_gpu=True,
        builder=args.builder,
        reference_dir=args.reference.resolve() if args.reference else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-run":
        config = _load_config(args.config)
        runs_root = args.runs_root if args.runs_root is not None else config.runs_root
        record = init_run(
            runs_root=runs_root,
            scope_src=args.scope,
            paper=Paper(arxiv_id=args.paper_id, title=args.title, url=args.url),
            today=args.date or date.today(),
            config=config,
        )
        print(f"created {record.run_dir}")
        return 0

    ctx = _context(parser, args)
    try:
        if args.command == "run":
            record = run_pipeline(args.run, ctx)
        else:
            record = run_stage(args.command, args.run, ctx)
    except Exception as exc:  # run.json on disk already holds the last completed stage
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1
    outcome = record.outcome.value if record.outcome else "none"
    print(f"stage: {record.stage}  outcome: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
