"""Report stage: summary.md. Commit, push, dashboard and notifications land in build step 6."""
from __future__ import annotations

from paper2code.llm.base import LLMError, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord, utcnow
from paper2code.manager.story import STORY_FILE, write_story
from paper2code.manager.verdict import VERDICT_JSON, Verdict


def render_summary(record: RunRecord, verdict: Verdict | None) -> str:
    outcome = record.outcome.value if record.outcome else "none"
    paper_line = f"- **Paper:** {record.paper.title or '(none)'} ({record.paper.arxiv_id or 'no id'}) {record.paper.url}"
    lines = [
        f"# Run {record.run_id}",
        "",
        paper_line.rstrip(),
        f"- **Outcome:** `{outcome}`",
        f"- **Stage reached:** {record.stage}",
        f"- **Test runs used:** {record.counters.test_runs_used} of {record.caps.test_runs}",
        f"- **GPU seconds:** {record.budget.gpu_seconds:.1f}",
        f"- **Spent:** {record.budget.spent_usd:.2f} USD of {record.budget.limit_usd:.2f} USD",
        f"- **Started:** {record.started_at}",
        f"- **Finished:** {record.finished_at}",
        "",
        "## Inspector",
        "",
    ]
    if verdict is None:
        lines.append("no verdict (run ended before inspection)")
    else:
        lines.append(verdict.summary)
        if verdict.confidence is not None:
            lines.append(f"- **Inspector confidence:** {verdict.confidence:.2f}")
        if verdict.flags:
            lines.append("")
            lines.append("Flags:")
            for f in verdict.flags:
                where = f"{f.file}:{f.line}" if f.line is not None else f.file
                lines.append(f"- `{f.kind}` ({f.source}) at {where}: {f.note}")
    if record.error is not None:
        lines += [
            "",
            "## Error",
            "",
            f"Stage `{record.error.stage}`, reason `{record.error.reason}`: {record.error.message}",
        ]
    return "\n".join(lines) + "\n"


def run(record: RunRecord, ctx: RunContext) -> None:
    record.finished_at = utcnow()
    verdict = Verdict.load(record.run_dir) if (record.run_dir / VERDICT_JSON).exists() else None
    summary_path = record.run_dir / "summary.md"
    summary_path.write_text(render_summary(record, verdict), encoding="utf-8")
    # The story is prose for readers; it never changes the outcome, so any failure is a note, not an error.
    # It is written once: a re-run of the report stage (a resumed run) must not re-bill or rewrite it.
    note = ""
    if (record.run_dir / STORY_FILE).exists():
        note = "story already present (use `paper2code story --run DIR` to rewrite it)"
    elif not (record.run_dir / "scope" / "spec.md").exists():
        note = "story not written: no scope (the run ended before an assignment was accepted)"
    else:
        try:
            write_story(record.run_dir, make_chat_model(ctx), Usage(), ctx.config.inspector_max_chars, record=record)
        except LLMError as exc:
            note = f"story not written: {exc}"
        except Exception as exc:
            note = f"story not written: {type(exc).__name__}: {exc}"
    if note:
        summary_path.write_text(summary_path.read_text(encoding="utf-8") + f"\n{note}\n", encoding="utf-8")
    else:
        summary_path.write_text(summary_path.read_text(encoding="utf-8") + f"\nStory: `{STORY_FILE}`\n", encoding="utf-8")
