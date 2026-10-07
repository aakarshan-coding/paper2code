"""paper2code command line. Local mode: no Modal; LLM calls go to OpenAI unless --llm fake."""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from paper2code.arxiv import http as arxiv_http  # module import so tests can monkeypatch make_polite_client
from paper2code.arxiv.api import fetch_by_id
from paper2code.config import Config, load_config
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.local import init_run, init_run_for_paper
from paper2code.manager.record import STAGES, Caps, Paper, create_run, stage_index

STAGE_COMMANDS = ("fetch", "score", "select", "build", "inspect", "report")
LLM_CHOICES = ("openai", "fake")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", type=Path, default=Path("config.yaml"))


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", type=Path, help="run directory, e.g. runs/2026-10-06")
    p.add_argument("--no-gpu", action="store_true", help="use a local subprocess instead of a Modal sandbox")
    p.add_argument("--builder", choices=["stub", "agent"], default="stub")
    p.add_argument("--reference", type=Path, help="reference implementation dir for --builder stub")
    p.add_argument("--llm", choices=LLM_CHOICES, default=None, help="override config llm (openai | fake)")
    _add_common(p)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper2code")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new-run", help="create an empty run directory for today")
    new.add_argument("--runs-root", type=Path)
    new.add_argument("--date", type=date.fromisoformat, default=None)
    _add_common(new)

    init = sub.add_parser("init-run", help="seed a run at the scope stage from a pre-written scope directory")
    init.add_argument("--scope", required=True, type=Path)
    init.add_argument("--paper-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--url", default="")
    init.add_argument("--runs-root", type=Path)
    init.add_argument("--date", type=date.fromisoformat, default=None)
    _add_common(init)

    run = sub.add_parser("run", help="run the pipeline from the run's current stage to the end (or --until)")
    _add_run_args(run)
    run.add_argument("--until", choices=STAGES, default=None, help="stop after this stage (dry run)")

    for name in STAGE_COMMANDS:
        sp = sub.add_parser(name, help=f"run only the {name} stage")
        _add_run_args(sp)
        if name == "score":
            sp.add_argument("--arxiv-id", help="score this one paper in a fresh run instead of --run")
            sp.add_argument("--runs-root", type=Path)
            sp.add_argument("--date", type=date.fromisoformat, default=None)
    return parser


def _load_config(path: Path) -> Config:
    return load_config(path) if path.exists() else Config()


def _reaches_build(command: str, until: str | None) -> bool:
    if command in ("build", "inspect", "report"):
        return True
    if command == "run":
        return until is None or stage_index(until) >= stage_index("build")
    return False


def _context(parser: argparse.ArgumentParser, args: argparse.Namespace, until: str | None = None) -> RunContext:
    config = _load_config(args.config)
    reaches_build = _reaches_build(args.command, until)
    if reaches_build and not args.no_gpu:
        parser.error("the Modal GPU sandbox lands in build step 4; pass --no-gpu")
    if reaches_build and args.builder == "stub" and args.reference is None:
        parser.error("--builder stub requires --reference DIR")
    return RunContext(
        config=config,
        no_gpu=True,
        builder=args.builder,
        reference_dir=args.reference.resolve() if args.reference else None,
        llm=args.llm or config.llm,
        until=until,
    )


def _caps(config: Config) -> Caps:
    return Caps(test_runs=config.caps.test_runs, wall_clock_s=config.caps.wall_clock_s, stall_n=config.caps.stall_n)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "new-run":
        config = _load_config(args.config)
        runs_root = args.runs_root if args.runs_root is not None else config.runs_root
        record = create_run(runs_root, args.date or date.today(), _caps(config), config.budget.limit_usd)
        print(f"created {record.run_dir}")
        return 0

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

    until = getattr(args, "until", None)
    ctx = _context(parser, args, until)

    if args.command == "score" and getattr(args, "arxiv_id", None):
        runs_root = args.runs_root if args.runs_root is not None else ctx.config.runs_root
        try:
            paper = fetch_by_id(args.arxiv_id, arxiv_http.make_polite_client(ctx))
        except Exception as exc:
            print(f"score failed: could not fetch {args.arxiv_id}: {exc}", file=sys.stderr)
            return 1
        record = init_run_for_paper(runs_root, paper, args.date or date.today(), ctx.config)
        print(f"created {record.run_dir}")
        args.run = record.run_dir
    elif args.run is None:
        parser.error("--run DIR is required")

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
