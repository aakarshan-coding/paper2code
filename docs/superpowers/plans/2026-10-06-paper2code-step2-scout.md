# paper2code Step 2: Fetch, Scout, Select — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build step 2 of the spec's build order: fetch the day's new arXiv papers, grade them with a two-pass scout (cheap abstract pass, then full-text scorecards for the top few), rank them with a selection policy, and record everything, so that `paper2code run --until select` works end to end in dry-run mode against live arXiv.

**Architecture:** Three new stage modules replace the step 1 stubs for `fetch`, `score`, and `select`. Fetch reads arXiv's per-category daily RSS feeds (one request per category, abstract inline) through a polite HTTP client that backs off on 429/503. Score calls an LLM behind a small `ChatModel` interface with two implementations: OpenAI structured outputs via `responses.parse`, and a fake for tests and offline runs. Every LLM call's tokens and dollars are added to the run record. Select applies a plain-Python policy from `policies/` and writes `selected.json`. All three stages run inside the existing resumable LangGraph nodes; a new `until` option on the run context stops the pipeline after a chosen stage.

**Tech Stack:** Python 3.11+, `httpx` (already installed, with `MockTransport` for tests), `openai>=3` with `pydantic` schemas for structured output, `pypdf` for PDF fallback, `pytest`. No Modal, no Agent SDK in this step.

**Spec:** `docs/superpowers/specs/2026-09-30-paper2code-design.md` (sections 5, 6, 7, 12, 13, 14)

**Facts established before planning (2026-10-06):**
- arXiv daily RSS: `https://rss.arxiv.org/rss/<category>`. Each `<item>` carries `guid` like `oai:arXiv.org:2610.03769v1`, a `description` of the form `arXiv:2610.03769v1 Announce Type: new \n Abstract: ...`, `<category>` elements, `dc:creator`, `pubDate`, and `<arxiv:announce_type>` in `new | cross | replace | replace-cross`. Today's cs.LG feed had 366 new, 234 cross, 331 replace items.
- The export API (`https://export.arxiv.org/api/query`) returns 503 and then `429 Rate exceeded` when hit repeatedly; a 25-second pause cleared it. It is used only for single-paper lookup by id.
- Full text: `https://arxiv.org/html/<id>v1` exists for current papers and contains an `<article>` element; `https://arxiv.org/pdf/<id>v1` is the fallback.
- OpenAI SDK 3.1.0: `client.responses.parse(model=, instructions=, input=, text_format=PydanticModel)` returns `.output_parsed` and `.usage.input_tokens / .output_tokens`. `OpenAI(max_retries=N)` retries 429/5xx itself.
- Prices (USD per million tokens, 2026-10): gpt-5.4-nano 0.20 in / 1.25 out; gpt-5.4-mini 0.75 / 4.50; gpt-5.5 5.00 / 30.00. Role assignment: scout pass one on gpt-5.4-nano, everything else on gpt-5.5. Rough cost per run at ~500 abstracts and 10 full texts: about 0.05 USD for pass one, about 1.00 USD for pass two.
- **The OpenAI account has no credits as of 2026-10-06** (`credit_balance_exhausted`). Everything in this plan is testable offline with the fake model and mocked HTTP. The final live-scoring check (Task 8, step 7) is blocked until credits are added.

## Global Constraints

- Python `>=3.11`; package under `src/paper2code/`, imported as `paper2code.*`; relative layout matches spec section 13 (`agents/scout/`, `llm/openai_client.py`, `policies/`).
- arXiv categories from config, initial `cs.LG, cs.CL, stat.ML` (spec section 2). Fetch is "metadata and abstract only" (spec section 5).
- Drop any arXiv id present in `seen.jsonl` at the root of `runs/` (spec section 5).
- Pass one output per paper is exactly `eligible: bool, reason: str` with reasons from the fixed vocabulary `no_quantitative_claim, survey_or_position, proprietary_data, too_large_to_run, not_a_method` (spec section 6). This plan adds `scoring_error` for a paper the model failed to grade; see Task 5.
- Pass two is capped at the top `max_fulltext_candidates` (default 10) by pass-one confidence (spec section 6). Scorecard fields exactly: `arxiv_id, title, testability: 1..5, difficulty: easy|medium|hard, est_gpu_hours, est_usd, claim, dataset, reason`.
- Both passes write to `candidates.jsonl`; every paper graded is kept, including rejections (spec section 6).
- Policies are plain Python functions in `policies/` with `NAME` and `VERSION` recorded in `selected.json`; shortlist of up to 3 (spec section 7). `select_v1_testability`: filter `est_usd <= budget.limit_usd`, sort by testability descending, then `est_usd` ascending.
- Outcome `no_candidates` means nothing passed eligibility that day (spec section 4.1). This plan also uses it when the shortlist is empty after the budget filter; see Task 7.
- `error` outcomes record `stage` and a reason; `api_error` is in the spec's reason list (spec section 4.1).
- Model ids live in config, chosen at implementation time (spec section 12). The `llm/openai_client.py` wrapper does model-per-role, retries, token accounting.
- Be polite to arXiv: identify with a User-Agent that includes a contact address, never more than one request every 3 seconds per client, back off on 429 and 503.
- Commit after every task with the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer. Run the suite with `TEMP`, `TMP`, `TMPDIR` pointed at the session scratchpad (sandbox quirk, see project memory).

## Review Focus

Failure modes the spec implies but does not spell out. Each line's test is pinned to the task named.

1. **arXiv answers 503 or 429** (it did during planning). Fetch must back off and retry, not crash the daily run, and must give up with a clear error after a bounded number of attempts. Pinned to Task 2 (`test_polite_client_backs_off_on_503_then_succeeds`, `test_polite_client_gives_up_after_max_attempts`).
2. **The same paper appears in two feeds, or as a replacement.** A cross-listed paper must be fetched once; `replace` and `replace-cross` items are not new submissions and must be excluded. Pinned to Task 2 (`test_fetch_daily_dedupes_across_feeds_and_skips_replacements`).
3. **The model returns verdicts for the wrong ids, or not all of them.** A missing id must be recorded as `scoring_error`, never silently dropped or attributed to another paper; an extra id must be ignored. Pinned to Task 5 (`test_pass_one_repairs_missing_and_ignores_extra_ids`).
4. **The API account has no credits or the API is down mid-score.** The run must end as `error` with reason `api_error`, keep every candidate row written so far, and record the tokens already spent. Pinned to Task 7 (`test_score_api_error_ends_run_as_error_and_keeps_partial_rows`).
5. **Full text is unavailable for one paper** (no HTML, PDF fails). That paper gets a pass-two row with an error note and the others are still scored. Pinned to Task 7 (`test_score_continues_when_one_fulltext_fails`).

---

### Task 1: Config, run context, and the `until` stop

**Files:**
- Modify: `src/paper2code/config.py`
- Modify: `config.yaml`
- Modify: `src/paper2code/manager/graph.py`
- Modify: `pyproject.toml` (add `pypdf`)
- Test: `tests/test_config.py`, `tests/test_graph.py`

**Interfaces:**
- Consumes: `Config`, `RunContext`, `make_node`, `_should_skip` from step 1.
- Produces:
  - `ModelPrice` frozen dataclass: `input_per_m: float`, `output_per_m: float`.
  - New `Config` fields: `llm: str = "openai"`, `pass_one_batch_size: int = 25`, `max_fulltext_chars: int = 80_000`, `shortlist_size: int = 3`, `gpu_usd_per_hour: float = 1.0`, `prices: dict[str, ModelPrice]`, and `models` now defaulting to `{"scout_pass1": "gpt-5.4-nano", "scout_pass2": "gpt-5.5", "scoper": "gpt-5.5", "inspector": "gpt-5.5", "builder": ""}`.
  - `RunContext` gains `llm: str = "openai"`, `until: str | None = None`, `http: Any = None`, `chat_model: Any = None`. The last two are test injection points; when `None`, stages build real clients from config.
  - `make_node` skips any stage after `ctx.until`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_default_models_and_prices():
    cfg = Config()
    assert cfg.models["scout_pass1"] == "gpt-5.4-nano"
    assert cfg.models["scout_pass2"] == "gpt-5.5"
    assert cfg.prices["gpt-5.4-nano"].input_per_m == 0.20
    assert cfg.prices["gpt-5.5"].output_per_m == 30.0
    assert cfg.llm == "openai"
    assert cfg.pass_one_batch_size == 25
    assert cfg.max_fulltext_chars == 80_000
    assert cfg.shortlist_size == 3
    assert cfg.gpu_usd_per_hour == 1.0


def test_repo_config_yaml_has_step2_keys():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert cfg.models["inspector"] == "gpt-5.5"
    assert cfg.prices["gpt-5.5"].input_per_m == 5.0
    assert cfg.llm == "openai"


def test_prices_yaml_override_merges_with_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("prices:\n  gpt-5.4-mini: {input: 0.75, output: 4.5}\nmodels:\n  scout_pass2: gpt-5.4-mini\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.prices["gpt-5.4-mini"].output_per_m == 4.5
    assert cfg.prices["gpt-5.5"].input_per_m == 5.0  # defaults kept
    assert cfg.models["scout_pass2"] == "gpt-5.4-mini"
    assert cfg.models["scout_pass1"] == "gpt-5.4-nano"  # defaults kept
```

Append to `tests/test_graph.py`:

```python
def test_until_stops_after_named_stage(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    ctx = RunContext(config=Config(runs_root=tmp_path), until="select")
    final = run_pipeline(rec.run_dir, ctx, _recording_stages(calls))
    assert calls == ["fetch", "score", "select"]
    assert final.stage == "select"
    assert final.outcome is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py tests/test_graph.py -q`
Expected: 4 failures: `KeyError: 'scout_pass1'`, `AttributeError: ... 'prices'`, and `TypeError: ... unexpected keyword argument 'until'`.

- [ ] **Step 3: Add `pypdf` to `pyproject.toml`**

In `[project] dependencies` add `"pypdf>=6",` after `"pyyaml>=6",`. Then `pip install -e ".[dev]"`.

- [ ] **Step 4: Update `src/paper2code/config.py`**

Replace the whole file:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BudgetConfig:
    limit_usd: float = 10.0


@dataclass(frozen=True)
class CapsConfig:
    test_runs: int = 25
    wall_clock_s: int = 7200
    stall_n: int = 5


@dataclass(frozen=True)
class ModelPrice:
    """USD per one million tokens."""

    input_per_m: float
    output_per_m: float


DEFAULT_MODELS: dict[str, str] = {
    "scout_pass1": "gpt-5.4-nano",
    "scout_pass2": "gpt-5.5",
    "scoper": "gpt-5.5",
    "inspector": "gpt-5.5",
    "builder": "",  # empty = Agent SDK default under the subscription
}

DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gpt-5.4-nano": ModelPrice(0.20, 1.25),
    "gpt-5.4-mini": ModelPrice(0.75, 4.50),
    "gpt-5.5": ModelPrice(5.00, 30.00),
}


@dataclass(frozen=True)
class Config:
    runs_root: Path = Path("runs")
    categories: list[str] = field(default_factory=lambda: ["cs.LG", "cs.CL", "stat.ML"])
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    caps: CapsConfig = field(default_factory=CapsConfig)
    min_seeds: int = 3
    max_fulltext_candidates: int = 10
    run_tests_timeout_s: int = 900
    gpu_type: str = "T4"
    max_tier: str = "5x"
    policy: str = "select_v1_testability"
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    prices: dict[str, ModelPrice] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    llm: str = "openai"  # "openai" or "fake"
    pass_one_batch_size: int = 25
    max_fulltext_chars: int = 80_000
    shortlist_size: int = 3
    gpu_usd_per_hour: float = 1.0


def load_config(path: Path) -> Config:
    """Load config.yaml. Missing keys fall back to the dataclass defaults; dict keys merge over defaults."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = Config()
    prices = dict(defaults.prices)
    for model_id, p in (raw.get("prices") or {}).items():
        prices[str(model_id)] = ModelPrice(input_per_m=float(p["input"]), output_per_m=float(p["output"]))
    return Config(
        runs_root=Path(raw.get("runs_root", defaults.runs_root)),
        categories=list(raw.get("categories", defaults.categories)),
        budget=BudgetConfig(**{**asdict(defaults.budget), **(raw.get("budget") or {})}),
        caps=CapsConfig(**{**asdict(defaults.caps), **(raw.get("caps") or {})}),
        min_seeds=int(raw.get("min_seeds", defaults.min_seeds)),
        max_fulltext_candidates=int(raw.get("max_fulltext_candidates", defaults.max_fulltext_candidates)),
        run_tests_timeout_s=int(raw.get("run_tests_timeout_s", defaults.run_tests_timeout_s)),
        gpu_type=str(raw.get("gpu_type", defaults.gpu_type)),
        max_tier=str(raw.get("max_tier", defaults.max_tier)),
        policy=str(raw.get("policy", defaults.policy)),
        models={**defaults.models, **{k: str(v) for k, v in (raw.get("models") or {}).items()}},
        prices=prices,
        llm=str(raw.get("llm", defaults.llm)),
        pass_one_batch_size=int(raw.get("pass_one_batch_size", defaults.pass_one_batch_size)),
        max_fulltext_chars=int(raw.get("max_fulltext_chars", defaults.max_fulltext_chars)),
        shortlist_size=int(raw.get("shortlist_size", defaults.shortlist_size)),
        gpu_usd_per_hour=float(raw.get("gpu_usd_per_hour", defaults.gpu_usd_per_hour)),
    )
```

- [ ] **Step 5: Update `config.yaml`**

Replace the `models:` block and append the new keys:

```yaml
llm: openai                 # openai | fake (fake = offline plumbing check, no API calls)
pass_one_batch_size: 25     # abstracts per scout pass-one call
max_fulltext_chars: 80000   # full text sent to pass two is cut here (~20k tokens)
shortlist_size: 3
gpu_usd_per_hour: 1.0       # used by the scout to turn est_gpu_hours into est_usd
models:
  scout_pass1: gpt-5.4-nano   # cheapest classifier, hundreds of abstracts a day
  scout_pass2: gpt-5.5        # strongest reasoning model, at most max_fulltext_candidates calls a day
  scoper: gpt-5.5
  inspector: gpt-5.5
  builder: ""                 # empty = Agent SDK default under the subscription
prices:                       # USD per 1M tokens, checked 2026-10-06
  gpt-5.4-nano: {input: 0.20, output: 1.25}
  gpt-5.4-mini: {input: 0.75, output: 4.50}
  gpt-5.5: {input: 5.00, output: 30.00}
```

- [ ] **Step 6: Update `src/paper2code/manager/graph.py`**

Change the imports and `RunContext`:

```python
from typing import Any, Callable, Mapping, TypedDict
```

```python
@dataclass(frozen=True)
class RunContext:
    """Everything a stage needs besides the record. Bound into the graph at build time."""

    config: Config
    no_gpu: bool = True
    builder: str = "stub"
    reference_dir: Path | None = None
    llm: str = "openai"  # "openai" or "fake"
    until: str | None = None  # stop after this stage (dry run); None runs to the end
    http: Any = None  # test injection: a PoliteClient; None builds one from config
    chat_model: Any = None  # test injection: a ChatModel; None builds one from config
```

And in `make_node`, replace the first skip check:

```python
        if _should_skip(record, stage):
            return {}
        if ctx.until is not None and stage_index(stage) > stage_index(ctx.until):
            return {}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_config.py tests/test_graph.py -q`
Expected: all pass (7 config, 11 graph).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml config.yaml src/paper2code/config.py src/paper2code/manager/graph.py tests/test_config.py tests/test_graph.py
git commit -m "Config for step 2: model ids, prices, llm switch; RunContext until/injection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: arXiv paper model, polite HTTP client, daily RSS feed

**Files:**
- Create: `src/paper2code/arxiv/__init__.py` (empty)
- Create: `src/paper2code/arxiv/models.py`
- Create: `src/paper2code/arxiv/http.py`
- Create: `src/paper2code/arxiv/feed.py`
- Create: `tests/fixtures/arxiv/rss_cs_LG.xml`
- Create: `tests/fixtures/arxiv/rss_stat_ML.xml`
- Test: `tests/test_arxiv_feed.py`

**Interfaces:**
- Consumes: nothing from this plan.
- Produces:
  - `ArxivPaper` dataclass: `arxiv_id: str` (no version), `version: int`, `title: str`, `abstract: str`, `authors: list[str]`, `categories: list[str]`, `primary_category: str`, `announce_type: str`, `published: str`, `url: str`; `to_dict()`, `from_dict(d)`; module functions `write_papers(path, papers)`, `read_papers(path) -> list[ArxivPaper]` (JSON lines).
  - `ArxivUnavailable(Exception)`.
  - `PoliteClient(client: httpx.Client | None = None, min_interval_s: float = 3.0, max_attempts: int = 5, sleep=time.sleep, contact: str = "")` with `get(url: str, params: dict | None = None) -> httpx.Response`. Retries on 429 and 503 with backoff 5, 10, 20, 40 seconds; raises `ArxivUnavailable` after `max_attempts`; returns any other response as is (callers check `status_code`).
  - `make_polite_client(ctx) -> PoliteClient` returning `ctx.http` when set.
  - `parse_rss(xml_bytes: bytes) -> list[ArxivPaper]`.
  - `fetch_daily(categories: list[str], http: PoliteClient) -> list[ArxivPaper]`: only `new` and `cross`, deduplicated by `arxiv_id`, in first-seen order.
  - `RSS_URL = "https://rss.arxiv.org/rss/{category}"`.

- [ ] **Step 1: Write the RSS fixtures**

`tests/fixtures/arxiv/rss_cs_LG.xml` (three items: one new, one cross, one replace):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <channel>
    <title>cs.LG updates on arXiv.org</title>
    <link>https://rss.arxiv.org/rss/cs.LG</link>
    <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
    <item>
      <title>Bayes-Sufficient Compression Is Not Enough</title>
      <link>https://arxiv.org/abs/2610.03769</link>
      <description>arXiv:2610.03769v1 Announce Type: new 
Abstract: Multi-agent LLM systems pair a sender with broad context and an executor with a limited local view. We study when communication helps.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.03769v1</guid>
      <category>cs.LG</category>
      <category>cs.IT</category>
      <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>new</arxiv:announce_type>
      <dc:creator>Ada Lovelace, Charles Babbage</dc:creator>
    </item>
    <item>
      <title>A Cross-Listed Paper</title>
      <link>https://arxiv.org/abs/2610.03800</link>
      <description>arXiv:2610.03800v1 Announce Type: cross 
Abstract: We propose a method and show it beats a baseline.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.03800v1</guid>
      <category>stat.ML</category>
      <category>cs.LG</category>
      <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>cross</arxiv:announce_type>
      <dc:creator>Grace Hopper</dc:creator>
    </item>
    <item>
      <title>An Old Paper, Revised</title>
      <link>https://arxiv.org/abs/2509.00001</link>
      <description>arXiv:2509.00001v3 Announce Type: replace 
Abstract: Revised version.</description>
      <guid isPermaLink="false">oai:arXiv.org:2509.00001v3</guid>
      <category>cs.LG</category>
      <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>replace</arxiv:announce_type>
      <dc:creator>Alan Turing</dc:creator>
    </item>
  </channel>
</rss>
```

`tests/fixtures/arxiv/rss_stat_ML.xml` (the cross-listed paper again as `new`, plus one more):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <channel>
    <title>stat.ML updates on arXiv.org</title>
    <link>https://rss.arxiv.org/rss/stat.ML</link>
    <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
    <item>
      <title>A Cross-Listed Paper</title>
      <link>https://arxiv.org/abs/2610.03800</link>
      <description>arXiv:2610.03800v1 Announce Type: new 
Abstract: We propose a method and show it beats a baseline.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.03800v1</guid>
      <category>stat.ML</category>
      <category>cs.LG</category>
      <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>new</arxiv:announce_type>
      <dc:creator>Grace Hopper</dc:creator>
    </item>
    <item>
      <title>Confidence Sequences in Higher Dimensions</title>
      <link>https://arxiv.org/abs/2610.03727</link>
      <description>arXiv:2610.03727v1 Announce Type: new 
Abstract: Modern sequential monitoring problems involve multiple metrics.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.03727v1</guid>
      <category>stat.ML</category>
      <category>stat.ME</category>
      <pubDate>Tue, 06 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>new</arxiv:announce_type>
      <dc:creator>Emmy Noether</dc:creator>
    </item>
  </channel>
</rss>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_arxiv_feed.py`:

```python
from pathlib import Path

import httpx
import pytest

from paper2code.arxiv.feed import RSS_URL, fetch_daily, parse_rss
from paper2code.arxiv.http import ArxivUnavailable, PoliteClient
from paper2code.arxiv.models import ArxivPaper, read_papers, write_papers

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _mock_http(routes, statuses=None):
    """routes: url -> bytes. statuses: list of status codes to serve in order for every request (default 200s)."""
    served = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(str(request.url))
        status = statuses.pop(0) if statuses else 200
        body = routes.get(str(request.url).split("?")[0], b"")
        return httpx.Response(status, content=body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PoliteClient(client=client, min_interval_s=0.0, sleep=lambda s: None), served


def test_parse_rss_extracts_fields():
    papers = parse_rss((FIX / "rss_cs_LG.xml").read_bytes())
    assert [p.arxiv_id for p in papers] == ["2610.03769", "2610.03800", "2509.00001"]
    first = papers[0]
    assert first.version == 1
    assert first.title == "Bayes-Sufficient Compression Is Not Enough"
    assert first.abstract.startswith("Multi-agent LLM systems")
    assert "communication helps." in first.abstract
    assert first.authors == ["Ada Lovelace", "Charles Babbage"]
    assert first.categories == ["cs.LG", "cs.IT"]
    assert first.primary_category == "cs.LG"
    assert first.announce_type == "new"
    assert first.url == "https://arxiv.org/abs/2610.03769"
    assert first.published == "Tue, 06 Oct 2026 00:00:00 -0400"
    assert papers[2].version == 3
    assert papers[2].announce_type == "replace"


def test_fetch_daily_dedupes_across_feeds_and_skips_replacements():
    http, served = _mock_http({
        RSS_URL.format(category="cs.LG"): (FIX / "rss_cs_LG.xml").read_bytes(),
        RSS_URL.format(category="stat.ML"): (FIX / "rss_stat_ML.xml").read_bytes(),
    })
    papers = fetch_daily(["cs.LG", "stat.ML"], http)
    assert [p.arxiv_id for p in papers] == ["2610.03769", "2610.03800", "2610.03727"]
    assert served == [RSS_URL.format(category="cs.LG"), RSS_URL.format(category="stat.ML")]
    assert all(p.announce_type in ("new", "cross") for p in papers)


def test_papers_jsonl_roundtrip(tmp_path):
    papers = parse_rss((FIX / "rss_cs_LG.xml").read_bytes())
    path = tmp_path / "papers.jsonl"
    write_papers(path, papers)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3
    assert read_papers(path) == papers
    assert isinstance(read_papers(path)[0], ArxivPaper)


def test_polite_client_backs_off_on_503_then_succeeds():
    sleeps = []
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] < 3 else 200, content=b"ok", request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=sleeps.append)
    resp = client.get("https://example.invalid/x")
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert sleeps == [5.0, 10.0]


def test_polite_client_gives_up_after_max_attempts():
    sleeps = []
    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, content=b"Rate exceeded.", request=r))),
        min_interval_s=0.0, max_attempts=3, sleep=sleeps.append,
    )
    with pytest.raises(ArxivUnavailable, match="429"):
        client.get("https://example.invalid/x")
    assert sleeps == [5.0, 10.0]


def test_polite_client_returns_non_retryable_statuses():
    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, request=r))),
        min_interval_s=0.0, sleep=lambda s: None,
    )
    assert client.get("https://example.invalid/missing").status_code == 404


def test_polite_client_spaces_requests():
    sleeps = []
    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r))),
        min_interval_s=3.0, sleep=sleeps.append,
    )
    client.get("https://example.invalid/a")
    client.get("https://example.invalid/b")
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 3.0


def test_polite_client_sends_contact_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None, contact="me@example.org")
    client.get("https://example.invalid/a")
    assert seen["ua"].startswith("paper2code/")
    assert "me@example.org" in seen["ua"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_arxiv_feed.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.arxiv'`

- [ ] **Step 4: Write `src/paper2code/arxiv/models.py`**

```python
"""Paper metadata as fetched from arXiv, and its JSON-lines persistence."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ArxivPaper:
    arxiv_id: str  # without version, e.g. "2610.03769"
    version: int
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    primary_category: str = ""
    announce_type: str = ""  # new | cross | replace | replace-cross | manual
    published: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ArxivPaper":
        return cls(**d)


def write_papers(path: Path, papers: list[ArxivPaper]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for p in papers:
            fh.write(json.dumps(p.to_dict()) + "\n")


def read_papers(path: Path) -> list[ArxivPaper]:
    if not path.exists():
        return []
    return [ArxivPaper.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
```

- [ ] **Step 5: Write `src/paper2code/arxiv/http.py`**

```python
"""A polite HTTP client for arXiv: identifies itself, spaces requests, backs off on 429/503."""
from __future__ import annotations

import time
from typing import Callable

import httpx

USER_AGENT_BASE = "paper2code/0.1"
RETRY_STATUSES = (429, 503)
BACKOFF_S = (5.0, 10.0, 20.0, 40.0, 80.0)


class ArxivUnavailable(Exception):
    """arXiv kept answering 429/503 for every attempt."""


class PoliteClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        min_interval_s: float = 3.0,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        contact: str = "",
    ) -> None:
        self.client = client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.min_interval_s = min_interval_s
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.user_agent = f"{USER_AGENT_BASE} (mailto:{contact})" if contact else USER_AGENT_BASE
        self._last_request_at: float | None = None

    def _wait_turn(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self.min_interval_s - elapsed
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        last_status = None
        for attempt in range(self.max_attempts):
            self._wait_turn()
            resp = self.client.get(url, params=params, headers={"User-Agent": self.user_agent}, follow_redirects=True)
            if resp.status_code not in RETRY_STATUSES:
                return resp
            last_status = resp.status_code
            if attempt < self.max_attempts - 1:
                self.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        raise ArxivUnavailable(f"{url}: {last_status} after {self.max_attempts} attempts")


def make_polite_client(ctx) -> PoliteClient:
    """The stage-facing factory. Tests set ctx.http; production builds a real client."""
    if ctx.http is not None:
        return ctx.http
    return PoliteClient(contact="sriramsattiraju@utexas.edu")
```

- [ ] **Step 6: Write `src/paper2code/arxiv/feed.py`**

```python
"""arXiv's daily per-category RSS feeds: the day's announcements with abstracts inline."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from paper2code.arxiv.http import PoliteClient
from paper2code.arxiv.models import ArxivPaper

RSS_URL = "https://rss.arxiv.org/rss/{category}"
NEW_TYPES = ("new", "cross")
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_VERSION_RE = re.compile(r"^(?P<id>.+?)v(?P<v>\d+)$")


def _split_version(versioned: str) -> tuple[str, int]:
    m = _VERSION_RE.match(versioned)
    if not m:
        return versioned, 1
    return m.group("id"), int(m.group("v"))


def _abstract_from_description(description: str) -> str:
    marker = "Abstract:"
    idx = description.find(marker)
    text = description[idx + len(marker):] if idx >= 0 else description
    return " ".join(text.split())


def parse_rss(xml_bytes: bytes) -> list[ArxivPaper]:
    root = ET.fromstring(xml_bytes)
    papers: list[ArxivPaper] = []
    for item in root.findall("./channel/item"):
        guid = item.findtext("guid") or ""
        arxiv_id, version = _split_version(guid.rsplit(":", 1)[-1])
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        creators = item.findtext(_DC_NS + "creator") or ""
        papers.append(ArxivPaper(
            arxiv_id=arxiv_id,
            version=version,
            title=" ".join((item.findtext("title") or "").split()),
            abstract=_abstract_from_description(item.findtext("description") or ""),
            authors=[a.strip() for a in creators.split(",") if a.strip()],
            categories=categories,
            primary_category=categories[0] if categories else "",
            announce_type=(item.findtext(_ARXIV_NS + "announce_type") or "").strip(),
            published=(item.findtext("pubDate") or "").strip(),
            url=(item.findtext("link") or "").strip(),
        ))
    return papers


def fetch_daily(categories: list[str], http: PoliteClient) -> list[ArxivPaper]:
    """New submissions and cross-lists across the categories, each paper once."""
    seen: dict[str, ArxivPaper] = {}
    for category in categories:
        resp = http.get(RSS_URL.format(category=category))
        resp.raise_for_status()
        for paper in parse_rss(resp.content):
            if paper.announce_type in NEW_TYPES and paper.arxiv_id not in seen:
                seen[paper.arxiv_id] = paper
    return list(seen.values())
```

Also create the empty `src/paper2code/arxiv/__init__.py`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_arxiv_feed.py -q`
Expected: 8 PASS

- [ ] **Step 8: Commit**

```bash
git add src/paper2code/arxiv tests/fixtures/arxiv tests/test_arxiv_feed.py
git commit -m "Add arXiv daily RSS fetch with a polite, backing-off HTTP client

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Single-paper lookup and full text

**Files:**
- Create: `src/paper2code/arxiv/api.py`
- Create: `src/paper2code/arxiv/fulltext.py`
- Create: `tests/fixtures/arxiv/api_by_id.xml`
- Create: `tests/fixtures/arxiv/paper.html`
- Test: `tests/test_arxiv_fulltext.py`

**Interfaces:**
- Consumes: `ArxivPaper`, `PoliteClient` (Task 2).
- Produces:
  - `API_URL = "https://export.arxiv.org/api/query"`; `fetch_by_id(arxiv_id: str, http: PoliteClient) -> ArxivPaper` (announce_type `"manual"`); raises `LookupError` when the API returns no entry.
  - `FullText` frozen dataclass: `text: str`, `source: str` (`"html"` or `"pdf"`), `truncated: bool`, `chars: int`.
  - `html_to_text(html: str) -> str`: text of the `<article>` element (whole body if there is none), scripts, styles and `<math>` annotation noise dropped, whitespace collapsed.
  - `pdf_to_text(data: bytes) -> str`.
  - `fetch_fulltext(arxiv_id: str, http: PoliteClient, max_chars: int) -> FullText`: tries `https://arxiv.org/html/{id}` then `https://arxiv.org/pdf/{id}`; raises `FullTextUnavailable` if both fail.
  - `FullTextUnavailable(Exception)`.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/arxiv/api_by_id.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title type="html">ArXiv Query: search_query=&amp;id_list=2610.03769</title>
  <entry>
    <id>http://arxiv.org/abs/2610.03769v1</id>
    <updated>2026-10-05T17:59:59Z</updated>
    <published>2026-10-05T17:59:59Z</published>
    <title>Bayes-Sufficient Compression Is Not Enough</title>
    <summary>  Multi-agent LLM systems pair a sender with broad context and an executor
with a limited local view. We study when communication helps.
</summary>
    <author><name>Ada Lovelace</name></author>
    <author><name>Charles Babbage</name></author>
    <link href="http://arxiv.org/abs/2610.03769v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2610.03769v1" rel="related" type="application/pdf"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.IT" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>
```

`tests/fixtures/arxiv/paper.html`:

```html
<!DOCTYPE html>
<html><head><title>Bayes-Sufficient Compression</title>
<style>.ltx_page { color: red }</style>
<script>window.x = 1;</script></head>
<body>
<nav>Skip to main content. Report GitHub Issue. arXiv is now an independent nonprofit!</nav>
<article class="ltx_document">
<h1 class="ltx_title">Bayes-Sufficient Compression Is Not Enough</h1>
<div class="ltx_abstract"><p>We study when communication helps multi-agent systems.</p></div>
<section><h2>1 Introduction</h2>
<p>Consider a sender and an executor. The executor sees <math alttext="x_t"><semantics><mi>x</mi><annotation>x_t</annotation></semantics></math> only.</p>
<p>Our method beats the baseline by 12 points on GSM8K.</p>
</section>
</article>
<footer>Copyright notice that should not appear.</footer>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

`tests/test_arxiv_fulltext.py`:

```python
from pathlib import Path

import httpx
import pytest

from paper2code.arxiv.api import API_URL, fetch_by_id
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext, html_to_text, pdf_to_text
from paper2code.arxiv.http import PoliteClient

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _client(handler):
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)


def _tiny_pdf() -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter

    w = PdfWriter()
    page = w.add_blank_page(width=200, height=200)
    # pypdf cannot author text simply; extract_text on a blank page returns "". We test the
    # dispatch path with the HTML route and the error path for PDF; pdf_to_text itself is
    # exercised on this blank page returning an empty string.
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_fetch_by_id_parses_atom_entry():
    def handler(request):
        assert str(request.url).startswith(API_URL)
        assert request.url.params["id_list"] == "2610.03769"
        return httpx.Response(200, content=(FIX / "api_by_id.xml").read_bytes(), request=request)

    paper = fetch_by_id("2610.03769", _client(handler))
    assert paper.arxiv_id == "2610.03769"
    assert paper.version == 1
    assert paper.title == "Bayes-Sufficient Compression Is Not Enough"
    assert paper.abstract.startswith("Multi-agent LLM systems")
    assert paper.authors == ["Ada Lovelace", "Charles Babbage"]
    assert paper.categories == ["cs.LG", "cs.IT"]
    assert paper.primary_category == "cs.LG"
    assert paper.announce_type == "manual"
    assert paper.url == "http://arxiv.org/abs/2610.03769v1"
    assert paper.published == "2026-10-05T17:59:59Z"


def test_fetch_by_id_raises_lookup_error_when_empty():
    empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>empty</title></feed>'
    with pytest.raises(LookupError):
        fetch_by_id("0000.00000", _client(lambda r: httpx.Response(200, content=empty, request=r)))


def test_html_to_text_keeps_article_drops_chrome():
    text = html_to_text((FIX / "paper.html").read_text(encoding="utf-8"))
    assert text.startswith("Bayes-Sufficient Compression Is Not Enough")
    assert "beats the baseline by 12 points on GSM8K" in text
    assert "x only." in text
    assert "Skip to main content" not in text
    assert "Copyright notice" not in text
    assert "window.x" not in text
    assert ".ltx_page" not in text
    assert "  " not in text


def test_html_to_text_without_article_uses_body():
    assert html_to_text("<html><body><p>Just a  body.</p></body></html>") == "Just a body."


def test_pdf_to_text_on_blank_page_is_empty():
    assert pdf_to_text(_tiny_pdf()) == ""


def test_fetch_fulltext_prefers_html_and_truncates():
    def handler(request):
        if "/html/" in str(request.url):
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        raise AssertionError("pdf should not be requested when html exists")

    ft = fetch_fulltext("2610.03769", _client(handler), max_chars=60)
    assert ft.source == "html"
    assert ft.truncated is True
    assert ft.chars == 60
    assert ft.text == html_to_text((FIX / "paper.html").read_text(encoding="utf-8"))[:60]


def test_fetch_fulltext_falls_back_to_pdf():
    def handler(request):
        if "/html/" in str(request.url):
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=_tiny_pdf(), headers={"content-type": "application/pdf"}, request=request)

    ft = fetch_fulltext("2610.03769", _client(handler), max_chars=1000)
    assert ft.source == "pdf"
    assert ft.truncated is False


def test_fetch_fulltext_raises_when_both_fail():
    with pytest.raises(FullTextUnavailable):
        fetch_fulltext("2610.03769", _client(lambda r: httpx.Response(404, request=r)), max_chars=1000)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_arxiv_fulltext.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.arxiv.api'`

- [ ] **Step 4: Write `src/paper2code/arxiv/api.py`**

```python
"""Single-paper lookup through the arXiv export API (Atom)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from paper2code.arxiv.feed import _split_version
from paper2code.arxiv.http import PoliteClient
from paper2code.arxiv.models import ArxivPaper

API_URL = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def fetch_by_id(arxiv_id: str, http: PoliteClient) -> ArxivPaper:
    resp = http.get(API_URL, params={"id_list": arxiv_id, "max_results": 1})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    entry = root.find("a:entry", _NS)
    if entry is None:
        raise LookupError(f"arXiv has no entry for {arxiv_id}")
    versioned = (entry.findtext("a:id", default="", namespaces=_NS) or "").rsplit("/", 1)[-1]
    bare_id, version = _split_version(versioned)
    categories = [c.get("term", "") for c in entry.findall("a:category", _NS)]
    primary = entry.find("arxiv:primary_category", _NS)
    alternate = next((l.get("href", "") for l in entry.findall("a:link", _NS) if l.get("rel") == "alternate"), "")
    return ArxivPaper(
        arxiv_id=bare_id,
        version=version,
        title=" ".join((entry.findtext("a:title", default="", namespaces=_NS) or "").split()),
        abstract=" ".join((entry.findtext("a:summary", default="", namespaces=_NS) or "").split()),
        authors=[(a.findtext("a:name", default="", namespaces=_NS) or "").strip() for a in entry.findall("a:author", _NS)],
        categories=categories,
        primary_category=primary.get("term", "") if primary is not None else (categories[0] if categories else ""),
        announce_type="manual",
        published=(entry.findtext("a:published", default="", namespaces=_NS) or "").strip(),
        url=alternate,
    )
```

- [ ] **Step 5: Write `src/paper2code/arxiv/fulltext.py`**

```python
"""Full text of a paper for scout pass two: arXiv's HTML rendering first, PDF as fallback."""
from __future__ import annotations

import io
from dataclasses import dataclass
from html.parser import HTMLParser

from pypdf import PdfReader

from paper2code.arxiv.http import PoliteClient

HTML_URL = "https://arxiv.org/html/{arxiv_id}"
PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
_SKIP_TAGS = {"script", "style", "annotation", "annotation-xml"}


class FullTextUnavailable(Exception):
    """Neither the HTML rendering nor the PDF could be fetched."""


@dataclass(frozen=True)
class FullText:
    text: str
    source: str  # "html" | "pdf"
    truncated: bool
    chars: int


class _ArticleText(HTMLParser):
    """Collects text inside <article> (or the whole <body> if there is no article)."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.article_depth = 0
        self.skip_depth = 0
        self.saw_article = False
        self.body_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self.article_depth += 1
            self.saw_article = True
        if tag in _SKIP_TAGS:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag in _SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.article_depth:
            self.parts.append(data)
        self.body_parts.append(data)

    def text(self) -> str:
        chosen = self.parts if self.saw_article else self.body_parts
        return " ".join(" ".join(chosen).split())


def html_to_text(html: str) -> str:
    parser = _ArticleText()
    parser.feed(html)
    return parser.text()


def pdf_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages).strip()


def _cut(text: str, max_chars: int, source: str) -> FullText:
    truncated = len(text) > max_chars
    text = text[:max_chars] if truncated else text
    return FullText(text=text, source=source, truncated=truncated, chars=len(text))


def fetch_fulltext(arxiv_id: str, http: PoliteClient, max_chars: int) -> FullText:
    resp = http.get(HTML_URL.format(arxiv_id=arxiv_id))
    if resp.status_code == 200 and resp.content:
        text = html_to_text(resp.text)
        if text:
            return _cut(text, max_chars, "html")
    resp = http.get(PDF_URL.format(arxiv_id=arxiv_id))
    if resp.status_code == 200 and resp.content:
        try:
            return _cut(pdf_to_text(resp.content), max_chars, "pdf")
        except Exception as exc:  # pypdf raises a zoo of exceptions on odd files
            raise FullTextUnavailable(f"{arxiv_id}: pdf parse failed: {exc}") from exc
    raise FullTextUnavailable(f"{arxiv_id}: html and pdf both unavailable")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_arxiv_fulltext.py -q`
Expected: 8 PASS

- [ ] **Step 7: Commit**

```bash
git add src/paper2code/arxiv/api.py src/paper2code/arxiv/fulltext.py tests/fixtures/arxiv tests/test_arxiv_fulltext.py
git commit -m "Add arXiv single-paper lookup and HTML/PDF full-text extraction

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: LLM layer: interface, OpenAI client, fake, cost accounting

**Files:**
- Create: `src/paper2code/llm/__init__.py` (empty)
- Create: `src/paper2code/llm/base.py`
- Create: `src/paper2code/llm/openai_client.py`
- Create: `src/paper2code/llm/fake.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `ModelPrice`, `Config` (Task 1).
- Produces:
  - `LLMError(Exception)`: any provider failure (quota, network, bad response), with the provider message.
  - `Usage` dataclass: `input_tokens: int = 0`, `output_tokens: int = 0`, `cost_usd: float = 0.0`, `calls: int = 0`; `add(other: Usage | LLMResult) -> None`.
  - `LLMResult(Generic[T])` frozen dataclass: `value: T`, `model: str`, `input_tokens: int`, `output_tokens: int`, `cost_usd: float`.
  - `ChatModel` Protocol: `parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]`.
  - `cost_usd(model: str, input_tokens: int, output_tokens: int, prices: Mapping[str, ModelPrice]) -> float`; raises `KeyError` for an unpriced model.
  - `OpenAIChatModel(models: Mapping[str, str], prices: Mapping[str, ModelPrice], client=None, max_retries: int = 3)`; constructor raises `ValueError` if any role's model has no price; `parse` wraps every `openai.OpenAIError` in `LLMError`.
  - `FakeChatModel(responder: Callable[[str, str, str, type[T]], T])`: returns `LLMResult` with `model="fake"`, `input_tokens=len(user)//4`, `output_tokens=50`, `cost_usd=0.0`; records every call in `.calls` as `(role, user, schema)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_llm.py`:

```python
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from paper2code.config import ModelPrice
from paper2code.llm.base import LLMError, LLMResult, Usage, cost_usd
from paper2code.llm.fake import FakeChatModel
from paper2code.llm.openai_client import OpenAIChatModel

PRICES = {"nano": ModelPrice(0.20, 1.25), "big": ModelPrice(5.0, 30.0)}
MODELS = {"scout_pass1": "nano", "scout_pass2": "big"}


class Answer(BaseModel):
    ok: bool
    note: str


def test_cost_usd_uses_per_million_prices():
    assert cost_usd("nano", 1_000_000, 0, PRICES) == pytest.approx(0.20)
    assert cost_usd("big", 100_000, 10_000, PRICES) == pytest.approx(0.5 + 0.3)
    with pytest.raises(KeyError):
        cost_usd("unknown", 1, 1, PRICES)


def test_usage_accumulates_results():
    u = Usage()
    u.add(LLMResult(Answer(ok=True, note=""), "nano", 100, 10, 0.001))
    u.add(LLMResult(Answer(ok=True, note=""), "big", 200, 20, 0.01))
    assert (u.input_tokens, u.output_tokens, u.calls) == (300, 30, 2)
    assert u.cost_usd == pytest.approx(0.011)
    other = Usage(input_tokens=1, output_tokens=1, cost_usd=1.0, calls=1)
    u.add(other)
    assert u.calls == 3 and u.cost_usd == pytest.approx(1.011)


class _StubResponses:
    def __init__(self, parsed, usage=(120, 8), raise_exc=None):
        self.parsed, self.usage, self.raise_exc, self.kwargs = parsed, usage, raise_exc, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(output_parsed=self.parsed, usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


def _stub_client(responses):
    return SimpleNamespace(responses=responses)


def test_openai_parse_routes_role_to_model_and_prices_it():
    stub = _StubResponses(Answer(ok=True, note="fine"), usage=(1_000_000, 100_000))
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(stub))
    result = llm.parse("scout_pass2", "be brief", "is this ok?", Answer)
    assert result.value == Answer(ok=True, note="fine")
    assert result.model == "big"
    assert (result.input_tokens, result.output_tokens) == (1_000_000, 100_000)
    assert result.cost_usd == pytest.approx(5.0 + 3.0)
    assert stub.kwargs["model"] == "big"
    assert stub.kwargs["instructions"] == "be brief"
    assert stub.kwargs["input"] == "is this ok?"
    assert stub.kwargs["text_format"] is Answer


def test_openai_rejects_unpriced_model_at_construction():
    with pytest.raises(ValueError, match="no price"):
        OpenAIChatModel({"scout_pass1": "mystery"}, PRICES, client=_stub_client(_StubResponses(None)))


def test_openai_rejects_unknown_role():
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(_StubResponses(None)))
    with pytest.raises(KeyError):
        llm.parse("nope", "", "", Answer)


def test_openai_wraps_provider_errors():
    import openai

    exc = openai.OpenAIError("You have no credits remaining.")
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(_StubResponses(None, raise_exc=exc)))
    with pytest.raises(LLMError, match="no credits"):
        llm.parse("scout_pass1", "", "x", Answer)


def test_openai_rejects_unparsed_output():
    stub = _StubResponses(None)
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(stub))
    with pytest.raises(LLMError, match="no parsed output"):
        llm.parse("scout_pass1", "", "x", Answer)


def test_fake_chat_model_records_calls_and_costs_nothing():
    fake = FakeChatModel(lambda role, instructions, user, schema: schema(ok=role == "scout_pass1", note=user))
    r = fake.parse("scout_pass1", "sys", "hello", Answer)
    assert r.value == Answer(ok=True, note="hello")
    assert r.model == "fake" and r.cost_usd == 0.0
    assert r.input_tokens == len("hello") // 4
    assert fake.calls == [("scout_pass1", "hello", Answer)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_llm.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.llm'`

- [ ] **Step 3: Write `src/paper2code/llm/base.py`**

```python
"""Provider-neutral LLM interface: a role name in, a parsed pydantic object plus usage out."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Mapping, Protocol, TypeVar

from pydantic import BaseModel

from paper2code.config import ModelPrice

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The provider failed: quota exhausted, network, refusal, or unparseable output."""


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, other: "Usage | LLMResult") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls if isinstance(other, Usage) else 1


class ChatModel(Protocol):
    def parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int, prices: Mapping[str, ModelPrice]) -> float:
    price = prices[model]  # KeyError on purpose: an unpriced model must not run unnoticed
    return (input_tokens * price.input_per_m + output_tokens * price.output_per_m) / 1_000_000
```

- [ ] **Step 4: Write `src/paper2code/llm/openai_client.py`**

```python
"""Thin OpenAI wrapper: model per role from config, SDK retries, token accounting. Spec section 13."""
from __future__ import annotations

from typing import Mapping

import openai

from paper2code.config import ModelPrice
from paper2code.llm.base import LLMError, LLMResult, T, cost_usd


class OpenAIChatModel:
    def __init__(
        self,
        models: Mapping[str, str],
        prices: Mapping[str, ModelPrice],
        client=None,
        max_retries: int = 3,
    ) -> None:
        for role, model in models.items():
            if model and model not in prices:
                raise ValueError(f"role {role!r} uses model {model!r} which has no price in config")
        self.models = dict(models)
        self.prices = dict(prices)
        self.client = client or openai.OpenAI(max_retries=max_retries)

    def parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]:
        model = self.models[role]
        try:
            resp = self.client.responses.parse(model=model, instructions=instructions, input=user, text_format=schema)
        except openai.OpenAIError as exc:
            raise LLMError(f"{model} ({role}): {exc}") from exc
        value = getattr(resp, "output_parsed", None)
        if value is None:
            raise LLMError(f"{model} ({role}): no parsed output (refusal or empty response)")
        usage = resp.usage
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResult(
            value=value,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(model, input_tokens, output_tokens, self.prices),
        )
```

- [ ] **Step 5: Write `src/paper2code/llm/fake.py`**

```python
"""A ChatModel for tests and offline runs. The responder decides the answer; nothing is billed."""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from paper2code.llm.base import LLMResult, T

Responder = Callable[[str, str, str, type[BaseModel]], BaseModel]


class FakeChatModel:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str, type[BaseModel]]] = []

    def parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]:
        self.calls.append((role, user, schema))
        value = self.responder(role, instructions, user, schema)
        return LLMResult(value=value, model="fake", input_tokens=len(user) // 4, output_tokens=50, cost_usd=0.0)
```

Also create the empty `src/paper2code/llm/__init__.py`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_llm.py -q`
Expected: 9 PASS

- [ ] **Step 7: Commit**

```bash
git add src/paper2code/llm tests/test_llm.py
git commit -m "Add LLM layer: ChatModel interface, OpenAI structured-output client, fake, cost accounting

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Scout: schemas, prompts, two passes, candidates file

**Files:**
- Create: `src/paper2code/agents/scout/__init__.py` (empty)
- Create: `src/paper2code/agents/scout/schemas.py`
- Create: `src/paper2code/agents/scout/prompts.py`
- Create: `src/paper2code/agents/scout/scout.py`
- Create: `src/paper2code/agents/scout/fake.py`
- Create: `src/paper2code/manager/candidates.py`
- Test: `tests/test_scout.py`

**Interfaces:**
- Consumes: `ArxivPaper` (Task 2), `ChatModel`, `LLMResult`, `Usage` (Task 4), `FakeChatModel` (Task 4).
- Produces:
  - `REJECTION_REASONS = ("no_quantitative_claim", "survey_or_position", "proprietary_data", "too_large_to_run", "not_a_method")` and `SCORING_ERROR = "scoring_error"`.
  - Pydantic `EligibilityVerdict(arxiv_id: str, eligible: bool, reason: str, confidence: float)`; `EligibilityBatch(verdicts: list[EligibilityVerdict])`; `Scorecard(testability: int, difficulty: str, est_gpu_hours: float, est_usd: float, claim: str, dataset: str, reason: str)`.
  - `PASS_ONE_INSTRUCTIONS: str`, `PASS_TWO_INSTRUCTIONS: str`, `render_pass_one_batch(papers) -> str`, `render_pass_two(paper, fulltext, gpu_usd_per_hour) -> str`.
  - `pass_one(papers: list[ArxivPaper], llm: ChatModel, batch_size: int, usage: Usage) -> list[EligibilityVerdict]` in input order, every paper covered.
  - `pass_two(paper: ArxivPaper, fulltext: str, llm: ChatModel, gpu_usd_per_hour: float, usage: Usage) -> Scorecard` with `testability` clamped to 1..5 and `difficulty` normalised to `easy|medium|hard`.
  - `fake_scout_responder(role, instructions, user, schema)`: pass one marks every id eligible with confidence 0.5; pass two returns testability 3, medium, 0.5 GPU hours, 0.5 USD. Used by the CLI's `--llm fake`.
  - `candidates.append_rows(path: Path, rows: list[dict]) -> None`, `candidates.read_rows(path: Path) -> list[dict]`, `candidates.pass_one_row(paper, verdict, model) -> dict`, `candidates.pass_two_row(paper, card, model, cost_usd, fulltext_source, fulltext_chars) -> dict`, `candidates.pass_two_error_row(paper, error) -> dict`. Rows carry `"pass": 1` or `"pass": 2`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scout.py`:

```python
import json

import pytest

from paper2code.agents.scout.fake import fake_scout_responder
from paper2code.agents.scout.prompts import PASS_ONE_INSTRUCTIONS, PASS_TWO_INSTRUCTIONS, render_pass_one_batch, render_pass_two
from paper2code.agents.scout.schemas import REJECTION_REASONS, SCORING_ERROR, EligibilityBatch, EligibilityVerdict, Scorecard
from paper2code.agents.scout.scout import pass_one, pass_two
from paper2code.arxiv.models import ArxivPaper
from paper2code.llm.base import Usage
from paper2code.llm.fake import FakeChatModel
from paper2code.manager import candidates


def _paper(i, title="T"):
    return ArxivPaper(arxiv_id=f"2610.{i:05d}", version=1, title=f"{title} {i}", abstract=f"Abstract {i}", url=f"https://arxiv.org/abs/2610.{i:05d}")


def test_rejection_vocabulary_matches_spec():
    assert REJECTION_REASONS == ("no_quantitative_claim", "survey_or_position", "proprietary_data", "too_large_to_run", "not_a_method")
    assert SCORING_ERROR == "scoring_error"


def test_render_pass_one_batch_lists_ids_titles_abstracts():
    text = render_pass_one_batch([_paper(1), _paper(2)])
    assert "2610.00001" in text and "2610.00002" in text
    assert "T 1" in text and "Abstract 2" in text
    assert "proprietary_data" in PASS_ONE_INSTRUCTIONS
    assert "testability" in PASS_TWO_INSTRUCTIONS


def test_render_pass_two_includes_fulltext_and_gpu_price():
    text = render_pass_two(_paper(7), "FULL TEXT HERE", gpu_usd_per_hour=1.5)
    assert "FULL TEXT HERE" in text and "1.5" in text and "T 7" in text


def test_pass_one_batches_and_preserves_order():
    seen_batches = []

    def responder(role, instructions, user, schema):
        assert role == "scout_pass1" and schema is EligibilityBatch
        ids = [line.split()[1] for line in user.splitlines() if line.startswith("ID:")]
        seen_batches.append(ids)
        return EligibilityBatch(verdicts=[EligibilityVerdict(arxiv_id=i, eligible=i.endswith("3"), reason="" if i.endswith("3") else "not_a_method", confidence=0.9) for i in ids])

    papers = [_paper(i) for i in range(1, 6)]
    usage = Usage()
    verdicts = pass_one(papers, FakeChatModel(responder), batch_size=2, usage=usage)
    assert seen_batches == [["2610.00001", "2610.00002"], ["2610.00003", "2610.00004"], ["2610.00005"]]
    assert [v.arxiv_id for v in verdicts] == [p.arxiv_id for p in papers]
    assert [v.eligible for v in verdicts] == [False, False, True, False, False]
    assert usage.calls == 3 and usage.cost_usd == 0.0


def test_pass_one_repairs_missing_and_ignores_extra_ids():
    def responder(role, instructions, user, schema):
        return EligibilityBatch(verdicts=[
            EligibilityVerdict(arxiv_id="2610.00001", eligible=True, reason="", confidence=1.7),  # out of range, clamp
            EligibilityVerdict(arxiv_id="9999.99999", eligible=True, reason="", confidence=0.5),  # not asked
        ])

    verdicts = pass_one([_paper(1), _paper(2)], FakeChatModel(responder), batch_size=10, usage=Usage())
    assert [v.arxiv_id for v in verdicts] == ["2610.00001", "2610.00002"]
    assert verdicts[0].eligible is True and verdicts[0].confidence == 1.0
    assert verdicts[1].eligible is False and verdicts[1].reason == SCORING_ERROR and verdicts[1].confidence == 0.0


def test_pass_one_normalises_unknown_reason():
    def responder(role, instructions, user, schema):
        return EligibilityBatch(verdicts=[EligibilityVerdict(arxiv_id="2610.00001", eligible=False, reason="boring", confidence=0.3)])

    v = pass_one([_paper(1)], FakeChatModel(responder), batch_size=10, usage=Usage())[0]
    assert v.reason == "not_a_method"


def test_pass_two_clamps_and_normalises():
    def responder(role, instructions, user, schema):
        assert role == "scout_pass2" and schema is Scorecard
        return Scorecard(testability=9, difficulty="Medium", est_gpu_hours=0.5, est_usd=0.5, claim="c", dataset="d", reason="r")

    usage = Usage()
    card = pass_two(_paper(1), "text", FakeChatModel(responder), gpu_usd_per_hour=1.0, usage=usage)
    assert card.testability == 5 and card.difficulty == "medium"
    assert usage.calls == 1


def test_fake_scout_responder_covers_both_passes():
    batch = fake_scout_responder("scout_pass1", "", render_pass_one_batch([_paper(1), _paper(2)]), EligibilityBatch)
    assert [v.arxiv_id for v in batch.verdicts] == ["2610.00001", "2610.00002"]
    assert all(v.eligible for v in batch.verdicts)
    card = fake_scout_responder("scout_pass2", "", "anything", Scorecard)
    assert card.testability == 3 and card.difficulty == "medium"


def test_candidates_rows_roundtrip(tmp_path):
    path = tmp_path / "candidates.jsonl"
    p = _paper(1)
    v = EligibilityVerdict(arxiv_id=p.arxiv_id, eligible=True, reason="", confidence=0.8)
    card = Scorecard(testability=4, difficulty="easy", est_gpu_hours=0.2, est_usd=0.2, claim="c", dataset="d", reason="r")
    candidates.append_rows(path, [candidates.pass_one_row(p, v, "nano")])
    candidates.append_rows(path, [candidates.pass_two_row(p, card, "big", 0.42, "html", 1234), candidates.pass_two_error_row(_paper(2), "fulltext_unavailable")])
    rows = candidates.read_rows(path)
    assert [r["pass"] for r in rows] == [1, 2, 2]
    assert rows[0] == {"pass": 1, "arxiv_id": "2610.00001", "title": "T 1", "eligible": True, "reason": "", "confidence": 0.8, "model": "nano"}
    assert rows[1]["testability"] == 4 and rows[1]["cost_usd"] == 0.42 and rows[1]["fulltext_source"] == "html" and rows[1]["fulltext_chars"] == 1234
    assert rows[1]["title"] == "T 1" and rows[1]["model"] == "big"
    assert rows[2] == {"pass": 2, "arxiv_id": "2610.00002", "title": "T 2", "error": "fulltext_unavailable"}
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["pass"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_scout.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.agents.scout'`

- [ ] **Step 3: Write `src/paper2code/agents/scout/schemas.py`**

```python
"""Structured-output schemas for the scout. Kept free of validators: OpenAI strict mode rejects
numeric constraints, so ranges are clamped in code after parsing."""
from __future__ import annotations

from pydantic import BaseModel

REJECTION_REASONS = ("no_quantitative_claim", "survey_or_position", "proprietary_data", "too_large_to_run", "not_a_method")
SCORING_ERROR = "scoring_error"  # the model did not return a verdict for this paper
DIFFICULTIES = ("easy", "medium", "hard")


class EligibilityVerdict(BaseModel):
    arxiv_id: str
    eligible: bool
    reason: str  # one of REJECTION_REASONS when not eligible; "" when eligible
    confidence: float  # 0..1, P(testability >= 4 after a full read)


class EligibilityBatch(BaseModel):
    verdicts: list[EligibilityVerdict]


class Scorecard(BaseModel):
    testability: int  # 1..5
    difficulty: str  # easy | medium | hard
    est_gpu_hours: float
    est_usd: float
    claim: str
    dataset: str
    reason: str
```

- [ ] **Step 4: Write `src/paper2code/agents/scout/prompts.py`**

```python
"""Scout prompts. Pass one grades abstracts in batches; pass two reads one full paper."""
from __future__ import annotations

from paper2code.arxiv.models import ArxivPaper

PASS_ONE_INSTRUCTIONS = """You are the scout for an automated research-reproduction loop. Each day the loop picks ONE new
arXiv paper, turns its main quantitative claim into a small experiment with tests, and has a coding
agent implement it on a single small GPU within about one GPU-hour at reduced scale.

For every paper below, decide whether it is ELIGIBLE for that loop, judging from the abstract alone.

A paper is eligible only if all of these hold:
- It proposes a concrete method, algorithm, or training recipe (not a survey, position paper, benchmark-only release, or theory-only result).
- It makes a quantitative claim: the method beats a baseline on a task by some measurable margin.
- The data it needs is freely downloadable (public datasets, synthetic data, or standard benchmarks).
- A scaled-down version is plausible on one small GPU in about an hour (small models, small subsets, few epochs).

If not eligible, give exactly one reason from this list:
- no_quantitative_claim: no measurable comparison against a baseline
- survey_or_position: survey, review, position, or opinion paper
- proprietary_data: needs data that cannot be downloaded freely
- too_large_to_run: needs large models, long training, or many GPUs even at reduced scale
- not_a_method: no method to implement (benchmark, dataset, theory, tooling, or analysis only)

Return one verdict per paper, using the exact ID given, and no verdicts for papers not listed.
For eligible papers set reason to an empty string. confidence is your probability, 0 to 1, that a
full read would rate the paper 4 or 5 out of 5 for testability (clear claim, clear baseline, small
data, simple method). Be strict: most papers are not eligible."""

PASS_TWO_INSTRUCTIONS = """You are the scout for an automated research-reproduction loop. A coding agent will try to implement
the paper's method at reduced scale on a single small GPU, and tests will check whether the paper's
main claim holds at that scale. Read the full paper and fill in a scorecard.

testability (1-5): how well the main claim can be turned into a pass/fail test at reduced scale.
  5: one clear claim, one clear baseline, public small data, simple method, result should be visible in minutes.
  4: as above but needs some scaling choices or a modest dataset download.
  3: claim is testable but depends on tuning, large data, or a long training run; margin may vanish at small scale.
  2: claim is vague, baseline is unclear, or the method needs resources the loop does not have.
  1: not testable in this setting.
difficulty: easy | medium | hard, for implementing the method itself from the paper.
est_gpu_hours: GPU hours for the scaled-down experiment (method plus baseline, all seeds).
est_usd: est_gpu_hours multiplied by the GPU price given in the input.
claim: one sentence in exactly this shape:
  "At reduced scale, <method> should beat <baseline> on <task> by at least <margin>."
dataset: the dataset name and whether it is freely downloadable (say "yes" or "no").
reason: two or three sentences on what makes this paper easy or hard to test, naming the key risk."""


def render_pass_one_batch(papers: list[ArxivPaper]) -> str:
    blocks = []
    for p in papers:
        blocks.append(f"ID: {p.arxiv_id}\nTitle: {p.title}\nAbstract: {p.abstract}\n")
    return f"{len(papers)} papers follow.\n\n" + "\n".join(blocks)


def render_pass_two(paper: ArxivPaper, fulltext: str, gpu_usd_per_hour: float) -> str:
    return (
        f"ID: {paper.arxiv_id}\nTitle: {paper.title}\nAuthors: {', '.join(paper.authors)}\n"
        f"GPU price: {gpu_usd_per_hour} USD per GPU hour\n\n"
        f"Abstract: {paper.abstract}\n\nFull text:\n{fulltext}"
    )
```

- [ ] **Step 5: Write `src/paper2code/agents/scout/scout.py`**

```python
"""The two scout passes. Pure functions over a ChatModel; no file writes here."""
from __future__ import annotations

from paper2code.agents.scout.prompts import PASS_ONE_INSTRUCTIONS, PASS_TWO_INSTRUCTIONS, render_pass_one_batch, render_pass_two
from paper2code.agents.scout.schemas import DIFFICULTIES, REJECTION_REASONS, SCORING_ERROR, EligibilityBatch, EligibilityVerdict, Scorecard
from paper2code.arxiv.models import ArxivPaper
from paper2code.llm.base import ChatModel, Usage

ROLE_PASS_ONE = "scout_pass1"
ROLE_PASS_TWO = "scout_pass2"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _normalise_verdict(v: EligibilityVerdict) -> EligibilityVerdict:
    reason = "" if v.eligible else (v.reason if v.reason in REJECTION_REASONS else "not_a_method")
    return EligibilityVerdict(arxiv_id=v.arxiv_id, eligible=v.eligible, reason=reason, confidence=_clamp01(v.confidence))


def pass_one(papers: list[ArxivPaper], llm: ChatModel, batch_size: int, usage: Usage) -> list[EligibilityVerdict]:
    """One verdict per paper, in input order. Papers the model skipped get a scoring_error verdict."""
    verdicts: list[EligibilityVerdict] = []
    for start in range(0, len(papers), batch_size):
        batch = papers[start:start + batch_size]
        result = llm.parse(ROLE_PASS_ONE, PASS_ONE_INSTRUCTIONS, render_pass_one_batch(batch), EligibilityBatch)
        usage.add(result)
        by_id = {v.arxiv_id: v for v in result.value.verdicts}
        for paper in batch:
            v = by_id.get(paper.arxiv_id)
            if v is None:
                verdicts.append(EligibilityVerdict(arxiv_id=paper.arxiv_id, eligible=False, reason=SCORING_ERROR, confidence=0.0))
            else:
                verdicts.append(_normalise_verdict(v))
    return verdicts


def pass_two(paper: ArxivPaper, fulltext: str, llm: ChatModel, gpu_usd_per_hour: float, usage: Usage) -> Scorecard:
    result = llm.parse(ROLE_PASS_TWO, PASS_TWO_INSTRUCTIONS, render_pass_two(paper, fulltext, gpu_usd_per_hour), Scorecard)
    usage.add(result)
    card = result.value
    difficulty = card.difficulty.strip().lower()
    return Scorecard(
        testability=max(1, min(5, int(card.testability))),
        difficulty=difficulty if difficulty in DIFFICULTIES else "medium",
        est_gpu_hours=max(0.0, float(card.est_gpu_hours)),
        est_usd=max(0.0, float(card.est_usd)),
        claim=card.claim.strip(),
        dataset=card.dataset.strip(),
        reason=card.reason.strip(),
    )
```

- [ ] **Step 6: Write `src/paper2code/agents/scout/fake.py`**

```python
"""Responder for FakeChatModel that plays the scout: everything eligible, middling scorecards."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.scout.schemas import EligibilityBatch, EligibilityVerdict, Scorecard


def fake_scout_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is EligibilityBatch:
        ids = [line.split(None, 1)[1].strip() for line in user.splitlines() if line.startswith("ID:")]
        return EligibilityBatch(verdicts=[EligibilityVerdict(arxiv_id=i, eligible=True, reason="", confidence=0.5) for i in ids])
    if schema is Scorecard:
        return Scorecard(
            testability=3, difficulty="medium", est_gpu_hours=0.5, est_usd=0.5,
            claim="At reduced scale, the method should beat the baseline on the task by at least a little.",
            dataset="unknown (fake scout)", reason="fake scout: no model was consulted",
        )
    raise ValueError(f"fake scout cannot answer schema {schema.__name__}")
```

- [ ] **Step 7: Write `src/paper2code/manager/candidates.py`**

```python
"""candidates.jsonl: one row per paper per scout pass. Append-only; every paper graded is kept."""
from __future__ import annotations

import json
from pathlib import Path

from paper2code.agents.scout.schemas import EligibilityVerdict, Scorecard
from paper2code.arxiv.models import ArxivPaper

CANDIDATES_FILE = "candidates.jsonl"


def append_rows(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pass_one_row(paper: ArxivPaper, verdict: EligibilityVerdict, model: str) -> dict:
    return {
        "pass": 1, "arxiv_id": paper.arxiv_id, "title": paper.title,
        "eligible": verdict.eligible, "reason": verdict.reason, "confidence": verdict.confidence, "model": model,
    }


def pass_two_row(paper: ArxivPaper, card: Scorecard, model: str, cost_usd: float, fulltext_source: str, fulltext_chars: int) -> dict:
    return {
        "pass": 2, "arxiv_id": paper.arxiv_id, "title": paper.title, **card.model_dump(),
        "model": model, "cost_usd": cost_usd, "fulltext_source": fulltext_source, "fulltext_chars": fulltext_chars,
    }


def pass_two_error_row(paper: ArxivPaper, error: str) -> dict:
    return {"pass": 2, "arxiv_id": paper.arxiv_id, "title": paper.title, "error": error}
```

Also create the empty `src/paper2code/agents/scout/__init__.py`.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_scout.py -q`
Expected: 9 PASS

- [ ] **Step 9: Commit**

```bash
git add src/paper2code/agents/scout src/paper2code/manager/candidates.py tests/test_scout.py
git commit -m "Add scout: eligibility and scorecard schemas, prompts, two passes, candidates.jsonl

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Selection policy

**Files:**
- Create: `src/paper2code/policies/__init__.py`
- Create: `src/paper2code/policies/select_v1_testability.py`
- Test: `tests/test_policies.py`

**Interfaces:**
- Consumes: nothing (policies work on plain dicts: the pass-two rows from `candidates.jsonl`).
- Produces:
  - Each policy module has `NAME: str`, `VERSION: str`, `select(cards: list[dict], limit_usd: float, k: int) -> list[dict]`.
  - `policies.get_policy(name: str)` returns the module; raises `KeyError` for an unknown name.

- [ ] **Step 1: Write the failing tests**

`tests/test_policies.py`:

```python
import pytest

from paper2code.policies import get_policy
from paper2code.policies import select_v1_testability as v1


def _card(i, testability, est_usd, error=None):
    row = {"pass": 2, "arxiv_id": f"2610.{i:05d}", "title": f"T{i}", "testability": testability, "est_usd": est_usd, "difficulty": "easy"}
    if error:
        row = {"pass": 2, "arxiv_id": f"2610.{i:05d}", "title": f"T{i}", "error": error}
    return row


def test_registry_resolves_v1():
    mod = get_policy("select_v1_testability")
    assert mod.NAME == "select_v1_testability" and mod.VERSION == "1"
    with pytest.raises(KeyError):
        get_policy("select_v9_nope")


def test_v1_filters_by_budget_then_sorts_testability_desc_cost_asc():
    cards = [_card(1, 5, 12.0), _card(2, 4, 3.0), _card(3, 5, 2.0), _card(4, 4, 1.0), _card(5, 3, 0.5), _card(6, 5, 9.0)]
    out = v1.select(cards, limit_usd=10.0, k=3)
    assert [c["arxiv_id"] for c in out] == ["2610.00003", "2610.00006", "2610.00004"]


def test_v1_skips_error_rows_and_handles_empty():
    assert v1.select([], 10.0, 3) == []
    assert v1.select([_card(1, 5, 1.0, error="fulltext_unavailable")], 10.0, 3) == []
    assert v1.select([_card(1, 5, 50.0)], 10.0, 3) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_policies.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'paper2code.policies'`

- [ ] **Step 3: Write `src/paper2code/policies/__init__.py`**

```python
"""Selection policies: plain functions over pass-two scorecard rows. Spec section 7."""
from __future__ import annotations

import importlib
from types import ModuleType

KNOWN = ("select_v1_testability",)


def get_policy(name: str) -> ModuleType:
    if name not in KNOWN:
        raise KeyError(f"unknown policy {name!r}; known: {', '.join(KNOWN)}")
    return importlib.import_module(f"paper2code.policies.{name}")
```

- [ ] **Step 4: Write `src/paper2code/policies/select_v1_testability.py`**

```python
"""v1: highest testability under budget, cheapest first among ties."""
from __future__ import annotations

NAME = "select_v1_testability"
VERSION = "1"


def select(cards: list[dict], limit_usd: float, k: int) -> list[dict]:
    scored = [c for c in cards if "testability" in c and "error" not in c]
    affordable = [c for c in scored if float(c.get("est_usd", float("inf"))) <= limit_usd]
    ranked = sorted(affordable, key=lambda c: (-int(c["testability"]), float(c["est_usd"])))
    return ranked[:k]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_policies.py -q`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/policies tests/test_policies.py
git commit -m "Add selection policy registry and select_v1_testability

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The fetch, score, and select stages

**Files:**
- Create: `src/paper2code/manager/seen.py`
- Create: `src/paper2code/llm/factory.py`
- Modify: `src/paper2code/manager/stages/fetch.py` (replace stub)
- Modify: `src/paper2code/manager/stages/score.py` (replace stub)
- Modify: `src/paper2code/manager/stages/select.py` (replace stub)
- Modify: `tests/test_graph.py` (one test changes meaning)
- Test: `tests/test_stages_scout.py`

**Interfaces:**
- Consumes: everything above, plus `RunRecord`, `Paper`, `Policy`, `RunError`, `Outcome`, `run_stage`, `run_pipeline`, `RunContext` from step 1.
- Produces:
  - `seen.load_seen(runs_root: Path) -> set[str]`, `seen.append_seen(runs_root: Path, arxiv_ids: list[str], run_id: str) -> None`; file `runs_root / "seen.jsonl"` with rows `{"arxiv_id", "run_id", "ts"}`.
  - `llm.factory.make_chat_model(ctx) -> ChatModel`: `ctx.chat_model` if set; else `"openai"` → `OpenAIChatModel(config.models, config.prices)`; `"fake"` → `FakeChatModel(fake_scout_responder)`; the name comes from `ctx.llm`.
  - `stages.fetch.run`: writes `papers.jsonl` (fetched papers minus seen); sets `outcome = no_candidates` if empty.
  - `stages.score.run`: reads `papers.jsonl`, runs pass one, writes pass-one rows, picks the top `max_fulltext_candidates` eligible by confidence, fetches full text and runs pass two for each, writes pass-two rows (or error rows), adds tokens and dollars to `record.budget`, appends all graded ids to `seen.jsonl`; on `LLMError` sets `outcome = error` with `RunError("score", "api_error", message)` keeping everything written so far; sets `outcome = no_candidates` when no pass-two scorecard exists.
  - `stages.select.run`: reads pass-two rows, applies `config.policy`, writes `selected.json` as `{"policy": {"name", "version"}, "shortlist": [rows]}`, sets `record.policy` and `record.paper` (top of shortlist); `outcome = no_candidates` when the shortlist is empty.
  - `PAPERS_FILE = "papers.jsonl"`, `SELECTED_FILE = "selected.json"`.

The existing `tests/test_graph.py::test_default_stages_before_scope_are_not_implemented_yet` expects `fetch` to raise `NotImplementedError`. After this task `fetch` is real and `scope` is the first stub, so that test must be rewritten to seed a run at `select` with a shortlist and expect `NotImplementedError` from `scope`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stages_scout.py`:

```python
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from paper2code.agents.scout.fake import fake_scout_responder
from paper2code.agents.scout.schemas import EligibilityBatch, EligibilityVerdict, Scorecard
from paper2code.arxiv.feed import RSS_URL
from paper2code.arxiv.fulltext import HTML_URL, PDF_URL
from paper2code.arxiv.http import PoliteClient
from paper2code.config import Config
from paper2code.llm.base import LLMError
from paper2code.llm.fake import FakeChatModel
from paper2code.manager import candidates
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, RunRecord, create_run
from paper2code.manager.seen import append_seen, load_seen
from paper2code.manager.stages.fetch import PAPERS_FILE
from paper2code.manager.stages.select import SELECTED_FILE

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _http(html_404_for=()):
    def handler(request):
        url = str(request.url)
        if url == RSS_URL.format(category="cs.LG"):
            return httpx.Response(200, content=(FIX / "rss_cs_LG.xml").read_bytes(), request=request)
        if url == RSS_URL.format(category="stat.ML"):
            return httpx.Response(200, content=(FIX / "rss_stat_ML.xml").read_bytes(), request=request)
        if "/html/" in url:
            if any(i in url for i in html_404_for):
                return httpx.Response(404, request=request)
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        if "/pdf/" in url:
            return httpx.Response(404, request=request)
        return httpx.Response(404, request=request)

    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)


def _scout(eligible_ids, testability_by_id=None, fail_pass_two_for=None):
    testability_by_id = testability_by_id or {}

    def responder(role, instructions, user, schema):
        if schema is EligibilityBatch:
            ids = [line.split(None, 1)[1].strip() for line in user.splitlines() if line.startswith("ID:")]
            return EligibilityBatch(verdicts=[
                EligibilityVerdict(arxiv_id=i, eligible=i in eligible_ids, reason="" if i in eligible_ids else "not_a_method", confidence=0.9 if i in eligible_ids else 0.1)
                for i in ids
            ])
        pid = user.splitlines()[0].split(None, 1)[1].strip()
        if fail_pass_two_for and pid == fail_pass_two_for:
            raise LLMError("You have no credits remaining.")
        return Scorecard(testability=testability_by_id.get(pid, 3), difficulty="easy", est_gpu_hours=0.5, est_usd=0.5, claim="c", dataset="d yes", reason="r")

    return FakeChatModel(responder)


def _ctx(tmp_path, http=None, llm=None, **cfg):
    config = Config(runs_root=tmp_path, categories=["cs.LG", "stat.ML"], **cfg)
    return RunContext(config=config, http=http or _http(), chat_model=llm, until="select")


def _new_run(tmp_path):
    return create_run(tmp_path, date(2026, 10, 6), Caps(), 10.0)


def test_seen_roundtrip(tmp_path):
    assert load_seen(tmp_path) == set()
    append_seen(tmp_path, ["a", "b"], "2026-10-06")
    append_seen(tmp_path, ["c"], "2026-10-07")
    assert load_seen(tmp_path) == {"a", "b", "c"}
    rows = [json.loads(l) for l in (tmp_path / "seen.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["run_id"] == "2026-10-06" and "ts" in rows[0]


def test_fetch_writes_papers_and_drops_seen(tmp_path):
    append_seen(tmp_path, ["2610.03727"], "earlier")
    rec = _new_run(tmp_path)
    final = run_stage("fetch", rec.run_dir, _ctx(tmp_path))
    assert final.stage == "fetch" and final.outcome is None
    rows = [json.loads(l) for l in (rec.run_dir / PAPERS_FILE).read_text(encoding="utf-8").splitlines()]
    assert [r["arxiv_id"] for r in rows] == ["2610.03769", "2610.03800"]


def test_fetch_with_nothing_new_is_no_candidates(tmp_path):
    append_seen(tmp_path, ["2610.03769", "2610.03800", "2610.03727"], "earlier")
    rec = _new_run(tmp_path)
    final = run_stage("fetch", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.NO_CANDIDATES


def test_score_two_passes_rows_budget_and_seen(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03727"}, {"2610.03727": 5}), max_fulltext_candidates=10)
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    p1 = [r for r in rows if r["pass"] == 1]
    p2 = [r for r in rows if r["pass"] == 2]
    assert [r["arxiv_id"] for r in p1] == ["2610.03769", "2610.03800", "2610.03727"]
    assert [r["eligible"] for r in p1] == [True, False, True]
    assert sorted(r["arxiv_id"] for r in p2) == ["2610.03727", "2610.03769"]
    assert {r["arxiv_id"]: r["testability"] for r in p2} == {"2610.03727": 5, "2610.03769": 3}
    assert all(r["fulltext_source"] == "html" and r["model"] == "fake" for r in p2)
    assert final.budget.spent_tokens > 0 and final.budget.spent_usd == 0.0
    assert load_seen(tmp_path) == {"2610.03769", "2610.03800", "2610.03727"}


def test_score_caps_full_text_by_confidence(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03800", "2610.03727"}), max_fulltext_candidates=2)
    run_stage("fetch", rec.run_dir, ctx)
    run_stage("score", rec.run_dir, ctx)
    p2 = [r for r in candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) if r["pass"] == 2]
    assert len(p2) == 2


def test_score_with_nothing_eligible_is_no_candidates(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout(set()))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is Outcome.NO_CANDIDATES
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    assert len(rows) == 3 and all(r["pass"] == 1 for r in rows)


def test_score_continues_when_one_fulltext_fails(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, http=_http(html_404_for=("2610.03769",)), llm=_scout({"2610.03769", "2610.03727"}))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    p2 = {r["arxiv_id"]: r for r in candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) if r["pass"] == 2}
    assert p2["2610.03769"]["error"].startswith("fulltext_unavailable")
    assert p2["2610.03727"]["testability"] == 3


def test_score_api_error_ends_run_as_error_and_keeps_partial_rows(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03727"}, fail_pass_two_for="2610.03727"))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is Outcome.ERROR
    assert (final.error.stage, final.error.reason) == ("score", "api_error")
    assert "no credits" in final.error.message
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    assert len([r for r in rows if r["pass"] == 1]) == 3  # pass one was kept
    assert final.budget.spent_tokens > 0  # pass-one usage was recorded before the failure
    assert final.stage == "score"


def test_select_writes_shortlist_and_sets_paper(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03800", "2610.03727"}, {"2610.03800": 5, "2610.03727": 4}), shortlist_size=2)
    final = run_pipeline(rec.run_dir, ctx)
    assert final.stage == "select" and final.outcome is None
    sel = json.loads((rec.run_dir / SELECTED_FILE).read_text(encoding="utf-8"))
    assert sel["policy"] == {"name": "select_v1_testability", "version": "1"}
    assert [c["arxiv_id"] for c in sel["shortlist"]] == ["2610.03800", "2610.03727"]
    assert final.policy.name == "select_v1_testability"
    assert final.paper.arxiv_id == "2610.03800" and final.paper.title == "A Cross-Listed Paper"
    assert final.paper.url == "https://arxiv.org/abs/2610.03800"


def test_select_over_budget_is_no_candidates(tmp_path):
    rec = create_run(tmp_path, date(2026, 10, 6), Caps(), limit_usd=0.1)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769"}))
    final = run_pipeline(rec.run_dir, ctx)
    assert final.outcome is Outcome.NO_CANDIDATES
    assert json.loads((rec.run_dir / SELECTED_FILE).read_text(encoding="utf-8"))["shortlist"] == []


def test_fake_llm_name_builds_fake_model(tmp_path):
    from paper2code.llm.factory import make_chat_model
    from paper2code.llm.fake import FakeChatModel as Fake

    ctx = RunContext(config=Config(runs_root=tmp_path), llm="fake")
    model = make_chat_model(ctx)
    assert isinstance(model, Fake)
    assert model.responder is fake_scout_responder
    with pytest.raises(ValueError, match="unknown llm"):
        make_chat_model(RunContext(config=Config(runs_root=tmp_path), llm="gemini"))
```

Replace `tests/test_graph.py::test_default_stages_before_scope_are_not_implemented_yet` with:

```python
def test_default_scope_stage_is_not_implemented_yet(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "select"
    rec.save()
    with pytest.raises(NotImplementedError, match="scope stage"):
        run_pipeline(rec.run_dir, _ctx(tmp_path))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_stages_scout.py tests/test_graph.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.manager.seen'` for the new file; the rewritten graph test passes already (scope still raises).

- [ ] **Step 3: Write `src/paper2code/manager/seen.py`**

```python
"""seen.jsonl at the root of runs/: every arXiv id the loop has graded, so it is never graded twice."""
from __future__ import annotations

import json
from pathlib import Path

from paper2code.manager.record import utcnow

SEEN_FILE = "seen.jsonl"


def load_seen(runs_root: Path) -> set[str]:
    path = runs_root / SEEN_FILE
    if not path.exists():
        return set()
    return {json.loads(line)["arxiv_id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def append_seen(runs_root: Path, arxiv_ids: list[str], run_id: str) -> None:
    runs_root.mkdir(parents=True, exist_ok=True)
    ts = utcnow()
    with (runs_root / SEEN_FILE).open("a", encoding="utf-8") as fh:
        for arxiv_id in arxiv_ids:
            fh.write(json.dumps({"arxiv_id": arxiv_id, "run_id": run_id, "ts": ts}) + "\n")
```

- [ ] **Step 4: Write `src/paper2code/llm/factory.py`**

```python
from __future__ import annotations

from paper2code.llm.base import ChatModel


def make_chat_model(ctx) -> ChatModel:
    """Stage-facing factory. Tests set ctx.chat_model; production builds from ctx.llm and config."""
    if ctx.chat_model is not None:
        return ctx.chat_model
    if ctx.llm == "openai":
        from paper2code.llm.openai_client import OpenAIChatModel

        return OpenAIChatModel(ctx.config.models, ctx.config.prices)
    if ctx.llm == "fake":
        from paper2code.agents.scout.fake import fake_scout_responder
        from paper2code.llm.fake import FakeChatModel

        return FakeChatModel(fake_scout_responder)
    raise ValueError(f"unknown llm {ctx.llm!r}; expected 'openai' or 'fake'")
```

- [ ] **Step 5: Replace `src/paper2code/manager/stages/fetch.py`**

```python
"""Fetch stage: the day's new papers in the configured categories, minus anything already seen."""
from __future__ import annotations

from paper2code.arxiv import http as arxiv_http  # module import so tests can monkeypatch make_polite_client
from paper2code.arxiv.feed import fetch_daily
from paper2code.arxiv.models import write_papers
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.seen import load_seen

PAPERS_FILE = "papers.jsonl"


def run(record: RunRecord, ctx: RunContext) -> None:
    http = arxiv_http.make_polite_client(ctx)
    papers = fetch_daily(ctx.config.categories, http)
    seen = load_seen(record.run_dir.parent)
    fresh = [p for p in papers if p.arxiv_id not in seen]
    write_papers(record.run_dir / PAPERS_FILE, fresh)
    if not fresh:
        record.outcome = Outcome.NO_CANDIDATES
```

- [ ] **Step 6: Replace `src/paper2code/manager/stages/score.py`**

```python
"""Score stage: scout pass one over abstracts, pass two over full text for the top few."""
from __future__ import annotations

from paper2code.agents.scout.scout import ROLE_PASS_ONE, ROLE_PASS_TWO, pass_one, pass_two
from paper2code.arxiv import http as arxiv_http  # module import so tests can monkeypatch make_polite_client
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext
from paper2code.arxiv.http import ArxivUnavailable
from paper2code.arxiv.models import read_papers
from paper2code.llm.base import LLMError, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager import candidates
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunError, RunRecord
from paper2code.manager.seen import append_seen
from paper2code.manager.stages.fetch import PAPERS_FILE


def _model_name(llm, role: str) -> str:
    models = getattr(llm, "models", None)
    return models[role] if models else "fake"


def run(record: RunRecord, ctx: RunContext) -> None:
    cfg = ctx.config
    run_dir = record.run_dir
    papers = read_papers(run_dir / PAPERS_FILE)
    llm = make_chat_model(ctx)
    http = arxiv_http.make_polite_client(ctx)
    out = run_dir / candidates.CANDIDATES_FILE
    usage = Usage()
    scorecards = 0
    try:
        verdicts = pass_one(papers, llm, cfg.pass_one_batch_size, usage)
        candidates.append_rows(out, [candidates.pass_one_row(p, v, _model_name(llm, ROLE_PASS_ONE)) for p, v in zip(papers, verdicts)])
        by_id = {p.arxiv_id: p for p in papers}
        eligible = sorted((v for v in verdicts if v.eligible), key=lambda v: -v.confidence)[: cfg.max_fulltext_candidates]
        for v in eligible:
            paper = by_id[v.arxiv_id]
            try:
                fulltext = fetch_fulltext(paper.arxiv_id, http, cfg.max_fulltext_chars)
            except (FullTextUnavailable, ArxivUnavailable) as exc:
                candidates.append_rows(out, [candidates.pass_two_error_row(paper, f"fulltext_unavailable: {exc}")])
                continue
            before = usage.cost_usd
            card = pass_two(paper, fulltext.text, llm, cfg.gpu_usd_per_hour, usage)
            candidates.append_rows(out, [candidates.pass_two_row(
                paper, card, _model_name(llm, ROLE_PASS_TWO), round(usage.cost_usd - before, 6), fulltext.source, fulltext.chars,
            )])
            scorecards += 1
    except LLMError as exc:
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="score", reason="api_error", message=str(exc))
    finally:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    append_seen(run_dir.parent, [p.arxiv_id for p in papers], record.run_id)
    if record.outcome is None and scorecards == 0:
        record.outcome = Outcome.NO_CANDIDATES
```

- [ ] **Step 7: Replace `src/paper2code/manager/stages/select.py`**

```python
"""Select stage: rank the scorecards with the configured policy, write selected.json."""
from __future__ import annotations

import json

from paper2code.manager import candidates
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Paper, Policy, RunRecord
from paper2code.policies import get_policy

SELECTED_FILE = "selected.json"


def run(record: RunRecord, ctx: RunContext) -> None:
    rows = [r for r in candidates.read_rows(record.run_dir / candidates.CANDIDATES_FILE) if r.get("pass") == 2]
    policy = get_policy(ctx.config.policy)
    shortlist = policy.select(rows, limit_usd=record.budget.limit_usd, k=ctx.config.shortlist_size)
    (record.run_dir / SELECTED_FILE).write_text(
        json.dumps({"policy": {"name": policy.NAME, "version": policy.VERSION}, "shortlist": shortlist}, indent=2),
        encoding="utf-8",
    )
    record.policy = Policy(name=policy.NAME, version=policy.VERSION)
    if not shortlist:
        record.outcome = Outcome.NO_CANDIDATES
        return
    top = shortlist[0]
    record.paper = Paper(arxiv_id=top["arxiv_id"], title=top.get("title", ""), url=f"https://arxiv.org/abs/{top['arxiv_id']}")
```

- [ ] **Step 8: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_stages_scout.py tests/test_graph.py -q` then `pytest -q`
Expected: 12 + 11 pass; whole suite green (the step 1 e2e tests still pass because `init-run` seeds at `scope` and skips these stages).

- [ ] **Step 9: Commit**

```bash
git add src/paper2code/manager/seen.py src/paper2code/llm/factory.py src/paper2code/manager/stages/fetch.py src/paper2code/manager/stages/score.py src/paper2code/manager/stages/select.py tests/test_stages_scout.py tests/test_graph.py
git commit -m "Add fetch, score and select stages with seen.jsonl, candidates.jsonl and selected.json

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: CLI for dry runs, single-paper scoring, live smoke test, docs

**Files:**
- Modify: `src/paper2code/cli.py`
- Modify: `src/paper2code/manager/local.py`
- Create: `tests/test_live_arxiv.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`
- Modify: `decisions.md` (journal entry for step 2; the executor writes it from the ledger at the end)

**Interfaces:**
- Consumes: `create_run`, `fetch_by_id`, `write_papers`, `PAPERS_FILE`, `run_pipeline`, `run_stage`, `RunContext`, `make_polite_client`.
- Produces:
  - `local.init_run_for_paper(runs_root: Path, paper: ArxivPaper, today: date, config: Config) -> RunRecord`: creates the run, writes `papers.jsonl` with that one paper, sets `stage = "fetch"`, saves.
  - CLI commands:
    - `new-run [--runs-root DIR] [--config PATH] [--date YYYY-MM-DD]` prints `created <run_dir>`.
    - `fetch --run DIR [--config PATH]`.
    - `score --run DIR [--llm openai|fake] [--config PATH]` **or** `score --arxiv-id ID [--llm] [--runs-root] [--date] [--config]` (creates a run for that one paper and scores it).
    - `select --run DIR [--config PATH]`.
    - `run --run DIR [--until STAGE] [--llm] [--no-gpu] [--builder] [--reference] [--config]`: `--no-gpu` and `--reference` are required only when the run will reach `build` (i.e. `--until` is absent or at `build` or later).
  - Exit codes as before: 0 ok, 2 usage, 1 pipeline exception.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
import httpx

from paper2code.arxiv.feed import RSS_URL
from paper2code.arxiv.http import PoliteClient
from paper2code.manager.outcomes import Outcome
from paper2code.manager.stages.fetch import PAPERS_FILE

FIX_ARXIV = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _patch_http(monkeypatch):
    def handler(request):
        url = str(request.url)
        if url.startswith("https://rss.arxiv.org/rss/cs.LG"):
            return httpx.Response(200, content=(FIX_ARXIV / "rss_cs_LG.xml").read_bytes(), request=request)
        if url.startswith("https://rss.arxiv.org/rss/"):
            return httpx.Response(200, content=b'<rss version="2.0"><channel><title>x</title></channel></rss>', request=request)
        if url.startswith("https://export.arxiv.org/api/query"):
            return httpx.Response(200, content=(FIX_ARXIV / "api_by_id.xml").read_bytes(), request=request)
        if "/html/" in url:
            return httpx.Response(200, content=(FIX_ARXIV / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)
    monkeypatch.setattr("paper2code.arxiv.http.make_polite_client", lambda ctx: client)


def test_new_run_then_dry_run_to_select_with_fake_llm(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    assert main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-06"]) == 0
    run_dir = tmp_path / "2026-10-06"
    assert capsys.readouterr().out.strip() == f"created {run_dir}"
    assert main(["run", "--run", str(run_dir), "--until", "select", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.stage == "select" and rec.outcome is None
    assert rec.paper.arxiv_id in ("2610.03769", "2610.03800")
    assert (run_dir / "candidates.jsonl").exists() and (run_dir / "selected.json").exists()
    assert "stage: select" in capsys.readouterr().out


def test_single_stage_commands_fetch_score_select(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-06"])
    run_dir = tmp_path / "2026-10-06"
    assert main(["fetch", "--run", str(run_dir)]) == 0
    assert RunRecord.load(run_dir).stage == "fetch"
    assert main(["score", "--run", str(run_dir), "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).stage == "score"
    assert main(["select", "--run", str(run_dir)]) == 0
    assert RunRecord.load(run_dir).stage == "select"


def test_score_by_arxiv_id_creates_single_paper_run(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    assert main(["score", "--arxiv-id", "2610.03769", "--llm", "fake", "--runs-root", str(tmp_path), "--date", "2026-10-06"]) == 0
    run_dir = tmp_path / "2026-10-06"
    rec = RunRecord.load(run_dir)
    assert rec.stage == "score" and rec.outcome is None
    papers = (run_dir / PAPERS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(papers) == 1 and "2610.03769" in papers[0]
    rows = (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2  # pass one and pass two for the one paper


def test_run_until_select_does_not_require_gpu_flags(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-06"])
    assert main(["run", "--run", str(tmp_path / "2026-10-06"), "--until", "fetch"]) == 0


def test_run_to_build_still_requires_no_gpu(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--run", str(run_dir), "--until", "build", "--builder", "stub", "--reference", str(canary_dir / "reference")])
    assert exc.value.code == 2
```

`tests/test_live_arxiv.py` (skipped unless opted in):

```python
"""Live smoke test against arXiv. Opt in with PAPER2CODE_LIVE=1; it makes real HTTP requests."""
import os

import pytest

from paper2code.arxiv.api import fetch_by_id
from paper2code.arxiv.feed import fetch_daily
from paper2code.arxiv.fulltext import fetch_fulltext
from paper2code.arxiv.http import PoliteClient

pytestmark = pytest.mark.skipif(os.environ.get("PAPER2CODE_LIVE") != "1", reason="set PAPER2CODE_LIVE=1 to hit arXiv")


def test_live_daily_feed_has_papers_with_abstracts():
    papers = fetch_daily(["stat.ML"], PoliteClient(contact="sriramsattiraju@utexas.edu"))
    assert len(papers) > 0
    assert all(p.arxiv_id and p.title and p.abstract for p in papers)
    assert all(p.announce_type in ("new", "cross") for p in papers)


def test_live_lookup_and_fulltext():
    http = PoliteClient(contact="sriramsattiraju@utexas.edu")
    paper = fetch_by_id("2610.03769", http)
    assert paper.title
    ft = fetch_fulltext(paper.arxiv_id, http, max_chars=5000)
    assert ft.chars == 5000 and ft.source in ("html", "pdf")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -q`
Expected: 5 new failures with `SystemExit: 2` (`invalid choice: 'new-run'`), the step 1 CLI tests still pass.

- [ ] **Step 3: Add `init_run_for_paper` to `src/paper2code/manager/local.py`**

Append:

```python
def init_run_for_paper(runs_root: Path, paper: "ArxivPaper", today: date, config: Config) -> RunRecord:
    """Create a run whose fetch stage is already done and holds exactly one paper (local `score --arxiv-id`)."""
    from paper2code.arxiv.models import write_papers
    from paper2code.manager.stages.fetch import PAPERS_FILE

    caps = Caps(test_runs=config.caps.test_runs, wall_clock_s=config.caps.wall_clock_s, stall_n=config.caps.stall_n)
    record = create_run(runs_root, today, caps, config.budget.limit_usd)
    write_papers(record.run_dir / PAPERS_FILE, [paper])
    record.stage = "fetch"
    record.save()
    return record
```

And add `from paper2code.arxiv.models import ArxivPaper` to the module's imports (top of file, after `Config`).

- [ ] **Step 4: Replace `src/paper2code/cli.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_cli.py -q` then `pytest -q`
Expected: 10 CLI tests pass; the live file reports 2 skipped; everything else green.

- [ ] **Step 6: Live smoke test against arXiv (no LLM, no credits needed)**

Run: `PAPER2CODE_LIVE=1 pytest tests/test_live_arxiv.py -q`
Expected: 2 PASS. If arXiv answers 503 or 429, the polite client backs off (up to about 75 seconds total); a final `ArxivUnavailable` means arXiv is throttling this network and the test should be retried later, not the code changed.

- [ ] **Step 7: Live dry run (needs OpenAI credits; BLOCKED as of 2026-10-06)**

Run from the repo root:

```bash
paper2code new-run
paper2code run --run runs/$(date +%F) --until select
cat runs/$(date +%F)/selected.json
```

Expected: `stage: select  outcome: none`, `candidates.jsonl` with one pass-one row per fetched paper and up to ten pass-two rows, `selected.json` with up to three entries, `run.json` showing `budget.spent_usd` of roughly 0.5 to 1.5 USD. If the account still has no credits, the run ends with `stage: score  outcome: error`, `run.json.error.reason == "api_error"`, and the pass-one rows are absent (the first call fails). Record whichever happened in the ledger; do not mark the task failed for a billing block, but say so in the final report.

Also run the offline equivalent so the plumbing is exercised end to end against real arXiv data without any model:

```bash
paper2code new-run --runs-root runs-fake
paper2code run --run runs-fake/$(date +%F) --until select --llm fake
```

Expected: `stage: select  outcome: none` and a `selected.json` with three fake-scored entries. Delete `runs-fake/` afterwards.

- [ ] **Step 8: Update `README.md`**

Replace the "Status" and "Local mode" sections with:

````markdown
## Status

Build step 2 of 6: fetch, scout and select work against live arXiv in dry-run
mode. The scope stage is not implemented yet; a full run still needs `init-run`
with a hand-written scope directory. Scout calls go to OpenAI and need credits
on the account; `--llm fake` exercises the plumbing without any model.

## Local mode

```bash
# Dry run: fetch today's papers, score them, pick a shortlist, stop.
paper2code new-run
paper2code run --run runs/<date> --until select            # real scout (OpenAI)
paper2code run --run runs/<date> --until select --llm fake # no model, plumbing only

# One stage at a time, or one paper at a time
paper2code fetch  --run runs/<date>
paper2code score  --run runs/<date>
paper2code score  --arxiv-id 2610.03769                     # fresh run with just this paper
paper2code select --run runs/<date>

# From a hand-written scope to completion (step 1 path)
paper2code init-run --scope tests/fixtures/canary/scope --paper-id canary-0001 --title "EMA denoising canary"
paper2code run --run runs/<date> --no-gpu --builder stub --reference tests/fixtures/canary/reference
```

A run directory holds `run.json`, `papers.jsonl`, `candidates.jsonl`,
`selected.json`, then `scope/` (frozen, with `manifest.json`), `workspace/`,
`build.log`, `verdict.json` and `summary.md`. `runs/seen.jsonl` lists every
paper ever graded. Re-running `run` on an existing run directory resumes at the
last completed stage. Set `PAPER2CODE_LIVE=1` to include the live arXiv smoke
test in `pytest`.
````

- [ ] **Step 9: Commit**

```bash
git add src/paper2code/cli.py src/paper2code/manager/local.py tests/test_cli.py tests/test_live_arxiv.py README.md
git commit -m "CLI: new-run, fetch/score/select stages, score --arxiv-id, run --until; live arXiv smoke test

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage for build step 2.** Section 5 fetch (categories from config, last 24 hours, metadata and abstract only, drop seen ids): Tasks 2 and 7. The RSS daily feed is the "last 24 hours" source; `new` and `cross` announcements are the day's submissions. Section 6 score: pass one over abstracts batched on a cheap model with the fixed reason vocabulary, pass two on the strongest model over full text capped at `max_fulltext_candidates`, scorecard fields, both passes into `candidates.jsonl`, rejections kept: Tasks 4, 5, 7. Section 7 select: policy function with `NAME`/`VERSION` in `selected.json`, shortlist of up to 3, `select_v1_testability` rule: Tasks 6, 7. Section 12 models per role in config, chosen at implementation time, with rough cost: Task 1. Section 13 layout (`agents/scout/`, `llm/openai_client.py`, `policies/`): Tasks 4, 5, 6. Section 14 local mode `paper2code fetch`, `paper2code score --arxiv-id`: Task 8. Section 16 step 2 ("Fetch, scout, select against live arXiv in dry-run mode, scorecards logged"): Task 8 steps 6 and 7, the latter blocked on credits.

**Deviations recorded.** `papers.jsonl` is a new run-record file between fetch and score (spec section 4 lists none; resume needs it). `scoring_error` is added to the pass-one reason vocabulary for a paper the model skipped. `no_candidates` is also used when the shortlist is empty after the budget filter. A pass-two row may be an error row (`"error": "fulltext_unavailable: ..."`) with no scorecard. `seen.jsonl` is appended at the end of `score`, so a paper counts as seen once graded. The `until` stop is a run-context option, not a spec feature. `src/paper2code/arxiv/` is a new package not in the spec's section 13 tree.

**Type consistency checked.** `PoliteClient.get(url, params)` returns `httpx.Response` and is used that way by `feed`, `api`, `fulltext`. `ChatModel.parse(role, instructions, user, schema) -> LLMResult` is used identically by `scout.pass_one/pass_two` and both implementations. `Usage.add` accepts `LLMResult` (Task 4) and is called with one in Task 5. `candidates.pass_two_row(paper, card, model, cost_usd, fulltext_source, fulltext_chars)` is the positional order used in Task 7. `policy.select(cards, limit_usd, k)` matches Tasks 6 and 7. `RunContext(config, no_gpu, builder, reference_dir, llm, until, http, chat_model)` is constructed with keywords everywhere. `init_run_for_paper(runs_root, paper, today, config)` matches Task 8's CLI call.

**Review Focus pinned.** 1 → Task 2 backoff tests; 2 → Task 2 dedupe test; 3 → Task 5 repair test; 4 → Task 7 api_error test; 5 → Task 7 fulltext-fails test.
