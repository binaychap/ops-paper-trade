# Ops Paper Trade — project memory

Last reviewed: 2026-09-05. Based on repository source inspection; commands and
runtime behavior were not tested during this documentation update.

## Purpose and stack

Python service that polls Optionomics trade ideas, builds deterministic trading
decisions, records them in SQLite, and prepares or submits Webull paper orders.
FastAPI supplies startup orchestration and a `/health` endpoint; this is a
polling service, not an incoming webhook application.

Python 3.12+, uv with `uv.lock`, FastAPI, Pydantic settings, python-dotenv, and
the Webull OpenAPI Python SDK. Development dependencies: pytest and Ruff.

## Source map

- `app/main.py`: FastAPI app, Settings, TradeIdea/TradingDecision models,
  polling thread, orchestration, risk helpers, and compatibility wrappers.
- `app/optionomics_client.py`: HTTP feed retrieval and environment loading.
- `app/optionomics.py`: feed model, confidence normalization, directional
  level validation, and decision builder returning dictionaries.
- `app/ledger.py`: SQLite schema, deduplication, status and audit persistence.
- `app/webull_submitter.py`: dry-run response and broker submission adapter.
- `app/webull-buy-combo-stock.py`: dynamically loaded equity bracket helper
  used by `webull_submitter.py`.
- `app/webull-buy-combo-option.py`: option bracket helper used by
  `app/main-option.py`.
- `app/webull_broker.py`: shared sandbox client, account lookup, and order IDs.
- `app/webull-option-chain.py`, `app/webull-buy-option.py`,
  `app/webull-client.py`, `app/main-option.py`: additional scripts; inspect
  their callers before treating them as part of the active service.
- `tests/test_apply_risk_gates.py`: regression coverage for decisions, risk
  gates, deduplication, order parameters, and rate-limit handling.
- `puml/`: architecture diagram sources and an image.
- `README.md`: setup and design overview; some descriptions are stale.

## Configuration and workflow

Settings reads `.env` and process environment. Defaults in source:

| Variable | Default |
| --- | --- |
| `DRY_RUN` | `true` |
| `MAX_NOTIONAL_USD` | `250` |
| `ALLOW_SHORT_SELLING` | `false` |
| `FORCE_REPROCESS` | `false` |
| `OPTIONOMICS_POLL_ENABLED` | `true` |
| `OPTIONOMICS_POLL_INTERVAL_SECONDS` | `600` |
| `WEBULL_ENDPOINT` | `api.sandbox.webull.com` |
| `DATABASE_PATH` | `bot.sqlite3` |

Feed credentials use `OPTIONOMICS_API_KEY` and `OPTIONOMICS_EMAIL`; the URL
uses `OPTIONOMICS_API_URL`. Broker credentials use `WEBULL_APP_KEY` and
`WEBULL_APP_SECRET`. Do not copy actual `.env` values into documentation.

Run from the repository root:

```bash
uv sync
PYTHONPATH=. uv run fastapi dev app/main.py
```

Startup launches a daemon polling thread when feed credentials are present
and polling is enabled. For local development without feed polling, prefix
the launch command with `OPTIONOMICS_POLL_ENABLED=false DRY_RUN=true`.

Development checks, to run as appropriate for code changes:

```bash
OPTIONOMICS_POLL_ENABLED=false DRY_RUN=true PYTHONPATH=. uv run pytest -q
PYTHONPATH=. uv run python -m compileall app
uv run ruff check app tests
```

These commands are guidance, not a recorded passing baseline.

## Behavior to preserve and understand

The polling path fetches ideas, checks trade-ID deduplication, inserts a queued
row, builds a decision, checks the symbol, creates a TradeIdea, and calls
`maybe_submit_order`. Outcomes include skipped, dry_run, ordered, and failed.

SQLite tables are `events` and `optionomics_trade_ideas`. Any existing trade-ID
row counts as seen regardless of status. `FORCE_REPROCESS` bypasses the initial
seen check; a separate ordered trade-ID/symbol check remains before submission.
Do not accidentally deduplicate the current idea against its newly queued row.

The feed builder requires entry, target, and stop levels. Bullish levels require
target > entry and stop < entry; bearish and neutral levels require target <
entry and stop > entry. Bearish decisions skip when shorting is disabled.
Neutral or Crush pipeline ideas receive the `iron_condor` strategy label;
other strategies currently receive `placeholder`. Confidence is normalized
to the range 0–1.

Dry-run submission returns before loading the broker helper. Non-dry-run
submission currently calls `buy_stock` with quantity 1 and entry/stop/target
prices; that helper builds an equity bracket order.

User-specified order pricing: stop price is 5% below entry (`entry * 0.95`)
and target price is 10% above entry (`entry * 1.10`), rounded to two decimal
places. `submit_paper_order` calculates these from entry even when the feed
supplies different stop/target levels. Feed decision validation still uses
the original feed levels.

## Known discrepancies to verify before related changes

These are observations from static source review, not fixes or a test report:

- README describes option-contract selection in the active submission flow,
  but `webull_submitter.py` currently uses the equity `buy_stock` helper.
  An `iron_condor` decision label does not establish multi-leg execution.
- `apply_risk_gates` exists but is not called by the current polling path or
  `maybe_submit_order`; do not assume its checks protect that path.
- Submission uses quantity 1 instead of sizing from the notional budget and
  calls `buy_stock` even for a `sell_short` decision. Returned side metadata
  alone does not establish the broker's actual order direction.
- The rate-limit log promises a retry on the next poll, but failed rows are
  still considered seen by the initial dedupe check under default settings.
- `app.main` wraps the feed builder to return TradingDecision, while the
  polling loop subsequently attempts `TradingDecision(**decision_data)`.
  Verify this model/dictionary boundary when debugging polling failures.
- Ledger serialization uses `json.dumps` directly on decisions, while main
  passes Pydantic models in several paths. Verify serialization before
  assuming those status writes succeed.
- Stock submission loads its helper directly through
  `app.webull_submitter._load_webull_stock_module`; tests patch that loader.
  The former recursive compatibility hook through `app.main` was removed.

## Local state and maintenance

`.env`, `.venv`, SQLite runtime state, logs, caches, and editor configuration
are local artifacts. Preserve existing runtime state during development and
use temporary databases for tests. No service or broker submission was started
to create this memory.

Keep this file focused on durable project facts and unresolved findings.
Update or remove findings after verification or fixes, recording relevant
validation without retaining a running transcript of every session.
