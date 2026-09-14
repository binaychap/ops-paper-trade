# Ops Paper Trade — project memory

Last reviewed: 2026-09-05. Scheduler implementation is covered by local fake-broker
tests; live sandbox execution has not been validated.

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
- `app/optionomics_client.py`: generic `fetch_json(api_url, user_email, api_key)`
  HTTP retrieval and environment loading. `fetch_trade_ideas` requires a caller-
  supplied `api_url` keyword and retains trade-idea response normalization.
  Both polling entry points pass `settings.optionomics_api_url`; `TopBullish`
  passes its flow URL to `fetch_json`. Successful 403 retries no longer raise
  the previous attempt's error; covered by mocked HTTP tests.
- `app/top-bullish.py`: standalone `TopBullish` client for `/api/v1/flow/bullish`.
  Run `uv run python app/top-bullish.py`; reads `OPTIONOMICS_EMAIL` and
  `OPTIONOMICS_API_KEY` from environment or `.env`, defaults to limit 10,
  and prints the original JSON response. Does not start trading workers.
  Reuses `optionomics_client.build_headers`: verified the bullish endpoint
  returns Cloudflare 1010/HTTP 403 with urllib's default user agent and HTTP 200
  with the existing client's browser headers (2026-09-13). Credentials are not logged.
- `app/optionomics.py`: feed model, confidence normalization, directional
  level validation, and decision builder returning dictionaries.
- `app/ledger.py`: SQLite schema, deduplication, status and audit persistence,
  scheduled exit jobs, and single-host worker exclusion.
- `app/exit_scheduler.py`: next-session calendar and persistent exit reconciliation.
- `app/stock_execution.py`: strict order/position queries, cancellation, and market sells.
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

## Scheduled stock exit implementation

User approved next-trading-day market exits. `NEXT_DAY_EXIT_ENABLED` defaults
false; `NEXT_DAY_EXIT_TIME=09:35`, `NEXT_DAY_EXIT_TIMEZONE=America/New_York`, and
`NEXT_DAY_EXIT_POLL_SECONDS=30`. `DRY_RUN=true` suppresses the worker and all
submission. Local `.env` values were preserved. See README for the runbook.

Uses `exchange-calendars` XNYS sessions and actual entry fill timestamps. The
worker persists bracket IDs before entry submission, derives a deadline after
confirmed fills, cancels outstanding entry/exit orders, confirms terminal states,
reconciles fills and holdings, and sells the tracked remainder at market. GTC
exit legs are requested only when scheduling is enabled; entry remains DAY.
No profit condition is imposed on the scheduled exit.

`ExitCalendar()` resolves omitted time/timezone arguments through `get_settings()`,
using `NEXT_DAY_EXIT_TIME` and `NEXT_DAY_EXIT_TIMEZONE` from `.env` or process
environment. Explicit constructor arguments override those configured values.

Jobs live in `scheduled_stock_exits`; state JSON retains IDs, deadline, quantities,
order snapshots, market attempts, and last error. One active job per account/symbol
is enforced by SQLite. A POSIX file lock beside the database excludes concurrent
entry submission and scheduler workers and releases on process death. Single-host
local SQLite is required. Existing positions/trades are not adopted automatically.

Scheduled entries require a flat stock position for the symbol. Unknown submission
results are reconciled by persisted client ID, never blindly replayed; if the ID
never becomes queryable, manual reconciliation is required. Confirmed terminal
partial exits can create a new market order for the remainder. Malformed or stale
responses and position shortfalls defer execution. Manual trades/corporate actions
can invalidate tracked ownership; use a dedicated paper account.

The polling model/dictionary boundary and Pydantic ledger serialization were fixed
as prerequisites. Submission fingerprints now use a stable trade-ID hash when
available. Scheduler tests use temporary databases and fake broker clients. Actual
sandbox GTC acceptance and response fields remain unverified; keep the scheduler
disabled until checked. No broker orders were placed during implementation.

## Trade status dashboard

`app/dashboard.py` serves a read-only dashboard at `/`, static CSS/JS from
`app/static/`, and a no-cache ledger snapshot at `/api/trades`. No frontend build
is required. The page has search/status/attention filters, 20-row pagination,
15-second optional refresh, and a details dialog. Trade-idea status and exit job
status remain distinct. Times display in New York time.

The snapshot reads SQLite in read-only mode in one transaction. It handles an
absent ledger or older schema without scheduler tables without initializing the
database. Jobs link by bracket entry ID or the stable trade-ID hash; never by
symbol alone. Unlinked jobs remain visible. Only selected fields are returned;
account IDs and raw feed payloads are omitted. The page shows saved observations,
not live broker state. Completed jobs can include unfilled cancelled entries.

Dashboard routes have no authentication; use the existing app locally. A UI-only
preview can use `uvicorn app.main:app --host 127.0.0.1 --port 8001 --lifespan off`
without starting trading workers. Configuration badges do not prove a worker is
running. Tests cover status pairing, orphan jobs, old/missing/corrupt databases,
read-only access, and withholding account/raw payload fields.

## Unconfirmed entry lookup handling (2026-09-07)

A pasted SDK HTTP 417 `OPENAPI_PARAM_ERR / Order not present` referred to a saved
master BUY. Read-only ledger inspection found a failed idea, a waiting_entry job,
no broker receipt, no recorded fill, and no scheduled exit date. The original
submission failure reason was not retained, so this does not establish whether
Webull rejected the entry or the submission result was otherwise ambiguous.

`StockExecution` now recognizes this specific missing-order error from either SDK
exceptions or HTTP responses. The scheduler preserves the unresolved job, performs
no new sale, and backs off lookups from 60 seconds to a maximum 15 minutes with a
persisted next_check_at. Successful reconciliation clears the backoff. Missing
orders are never treated as cancelled, unfilled, or complete. Future entry
submission exceptions are retained on the exit job for troubleshooting; the UI
shows that error and the next lookup time. No existing jobs were edited or deleted.

Shared SDK logging now uses INFO, disables propagation to the root logger, and
replaces core client ERROR dumps (which can contain signed request headers) with
a short message. Application errors retain the actionable reason.

## Bullish flow stock runner

`app/main-top-bullish.py` provides `MainTopBullish.run(limit=10)`, a one-shot
runner loading `TopBullish` from `app/top-bullish.py` and calling the existing
`webull-buy-combo-stock.py` helper. Each feed entry uses a Webull snapshot price
for a one-share LIMIT entry, a 5% stop and 10% target (two decimal places), with
DAY exits. Entries above `MAX_NOTIONAL_USD` are skipped. It does not start the
existing polling service or next-day exit worker.

`app/webull_quotes.py` uses the installed SDK's `DataClient.market_data.get_snapshot`
and validates symbol, positive finite price, and `last_trade_time` (milliseconds);
quotes older than five minutes or over five seconds in the future are rejected.
`webull_broker.py` shares a cached signed sandbox API client between trade/data
clients. Live quote permissions and sandbox snapshot behavior remain unverified.

`BullishLedger` creates `top_bullish_trades` in `DATABASE_PATH` on initialization.
It stores feed metrics/payload, quote, order parameters, status, broker tracking
IDs/response, exception type and timestamps. Atomic unique trade-ID and symbol
claims precede broker calls; tracking IDs are persisted through `before_submit`.
Feed entries without an ID use `bullish:SYMBOL`. Deduplication is permanent within
this table, including dry runs, failed attempts and unknown submissions; it does
not check other strategies' tables or current broker positions. Quote/budget
skips are returned without reserving a symbol, allowing later valid attempts.
Unknown submissions require manual reconciliation and are never auto-retried.

Bullish runner logs quote/order failures with symbol and SDK HTTP status, error
code and message, also included in final JSON. `app/webull_errors.py` formats
only selected SDK exception fields and redacts configured secrets; raw SDK
request logging remains suppressed. Unexpected exceptions retain type-only
reporting. Non-200 quote responses expose selected error fields as well.

Preview with `DRY_RUN=true DATABASE_PATH=/tmp/bullish-preview.sqlite3 uv run python
app/main-top-bullish.py --limit 10`. The preview uses real feed/quote requests but
never submits orders; a separate database avoids reserving production symbols.
`DRY_RUN=false` enables sandbox submission. Verified with temporary databases and
mocked feed/quote/broker calls: full suite 84 passed; no broker orders placed.

SE read-only sandbox snapshot check on 2026-09-13 returned a quote about 38 hours
old, rejected by the five-minute freshness limit. `QuoteError` now exposes safe
validation details (including age/limit) in runner skip reasons; unexpected SDK
exceptions still show only their type. No freshness limit was relaxed or order
submitted during diagnosis.
