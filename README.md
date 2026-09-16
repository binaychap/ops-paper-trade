# Ops Paper Trade

This project is a paper-trading automation loop that consumes Optionomics trade ideas, validates them against a deterministic risk model, and then prepares or submits a paper order through Webull. It is designed to run as a polling service rather than an external listener.

The core entry point is [app/main.py](app/main.py). It manages configuration, fetches trade ideas, performs decision logic, stores state in SQLite, and starts the polling background worker.

## What this project does

- polls the Optionomics trade-idea API on a timer
- validates and normalizes incoming trade-idea payloads
- chooses a trading action such as buy, sell short, or skip
- enforces risk gates like symbol consistency, price-level sanity, and max notional caps
- deduplicates by trade ID to avoid repeat submissions
- resolves a valid Webull option contract from the option chain
- submits either a dry-run result or a paper order through Webull
- writes execution and decision state into SQLite for later inspection

## Architecture

The app is intentionally simple and layered around a few core pieces:

- [app/optionomics_client.py](app/optionomics_client.py): fetches trade ideas from the Optionomics API
- [app/main.py](app/main.py): orchestrates validation, risk gates, execution, and startup logic
- [app/webull-buy-combo-stock.py](app/webull-buy-combo-stock.py): stock bracket order submission used by the polling service
- [app/webull-buy-combo-option.py](app/webull-buy-combo-option.py): option bracket order submission used by `app/main-option.py`
- [app/webull_broker.py](app/webull_broker.py): shared sandbox client, account lookup, and order IDs
- [app/webull-option-chain.py](app/webull-option-chain.py): option-chain lookup utilities
- [bot.sqlite3](bot.sqlite3): local SQLite ledger used for dedupe and auditing
- [tests/test_apply_risk_gates.py](tests/test_apply_risk_gates.py): regression tests for the decision/risk logic

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- an Optionomics account with API access and a valid email + API token
- a Webull paper/sandbox account with API credentials
- optionally, a local `.env` file for runtime values

## Environment setup

Create a `.env` file in the project root:

```env
DRY_RUN=true
MAX_NOTIONAL_USD=250
ALLOW_SHORT_SELLING=false
FORCE_REPROCESS=false

OPTIONOMICS_API_KEY=your-optionomics-api-key
OPTIONOMICS_EMAIL=you@example.com
OPTIONOMICS_API_URL=https://optionomics.ai/api/v1/trade_ideas
OPTIONOMICS_POLL_ENABLED=true
OPTIONOMICS_POLL_INTERVAL_SECONDS=600

WEBULL_APP_KEY=your-webull-app-key
WEBULL_APP_SECRET=your-webull-app-secret
WEBULL_ENDPOINT=api.sandbox.webull.com

DATABASE_PATH=bot.sqlite3
```

## Run locally

For Oracle VM setup, networking, SSH, service configuration and backups, see
[deployment.md](deployment.md).

```bash
uv sync
PYTHONPATH=. uv run fastapi dev app/main.py

 DRY_RUN=true DATABASE_PATH=/tmp/bullish-preview.sqlite3 uv run python app/main-top-bullish.py

```

Or run the application directly with the repo on the Python path:

```bash
PYTHONPATH=. python app/main.py
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

### Run test

```
cd /Users/binayrai/github/ops-paper-trade && PYTHONPATH=. .venv/bin/python -m pytest tests/test_exit_scheduler.py -q
```

## Trade status dashboard

Open **http://127.0.0.1:8000/** while the FastAPI service is running. The page
shows trade-idea status and scheduled exit status side by side, with symbol/ID
search, status filters, an attention filter, pagination, and optional refresh
every 15 seconds. Select **View** for recorded fills, broker order references,
next-day deadline, decision rationale, and the latest reconciliation error.
All displayed timestamps use New York time.

The dashboard reads the existing SQLite ledger through `GET /api/trades` and
never places orders or queries the broker. It does not create a database when
none exists. An `ordered` idea is not proof of an open position; `complete`
means the tracked exit workflow finished, including cancelled unfilled entries.
Untracked trades show no inferred broker fill status. Standalone exit jobs
remain visible even without a corresponding trade-idea row.

The page is served by the same local FastAPI app; it has no separate frontend
build or hosted copy of your ledger. Treat the app as a local tool: these routes
do not add authentication. To preview the dashboard without starting either
trading worker, run:

```bash
PYTHONPATH=. uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --lifespan off
```

Then open http://127.0.0.1:8001/. The scheduler badge represents configuration,
not proof that a worker is running; this preview command disables startup hooks.

## Scheduled next-trading-day stock exits

The optional scheduler exits tracked long stock trades at market on the next
NYSE trading session after an entry fill, defaulting to **9:35 AM New York time**.
It sells regardless of profit or loss. It handles weekends, exchange holidays,
DST, and early closes through `exchange-calendars` (`XNYS`). A missed exit is
processed during the next available regular session while the app is running.
The poll interval means execution is not guaranteed at the exact scheduled second.

```env
NEXT_DAY_EXIT_ENABLED=false
NEXT_DAY_EXIT_TIME=09:35
NEXT_DAY_EXIT_TIMEZONE=America/New_York
NEXT_DAY_EXIT_POLL_SECONDS=30
```

The feature is disabled by default. `DRY_RUN=true` prevents both scheduler
startup and broker submissions. After sandbox validation, set
`NEXT_DAY_EXIT_ENABLED=true` and `DRY_RUN=false` and restart the service to use it.
The worker runs independently of Optionomics feed polling. No `.env` values
were changed when this feature was added.

When enabled, new stock brackets retain `DAY` for the entry and use `GTC` for
both exit legs. The stop remains 5% below entry and the target 10% above entry.
Bracket IDs are stored **before** submission in `scheduled_stock_exits`; the
actual broker fill timestamp determines the scheduled date. Unfilled entries
have no exit deadline. Only newly tracked orders are managed; existing ledger
rows and positions are not automatically adopted.

Before the scheduled market sale, the worker cancels any entry remainder and
outstanding bracket exits, queries their final statuses, and subtracts all
confirmed bracket and earlier market-exit fills from the entry quantity. It
checks the current stock position and submits only the tracked remainder as a
normal `SELL / MARKET / DAY / CORE` order. A bracket that already closed the
trade completes the job without another sale. Partial market fills remain under
observation; a replacement for the remainder is possible only after the prior
order is confirmed terminal.

A persistent market-order ID is committed before each submission. If a request
times out, the worker looks up that ID on restart instead of blindly resubmitting.
The specific Webull “Order not present” error backs off lookups from 60 seconds
to 15 minutes; the dashboard shows the next lookup time and, for new submission
failures, the original submission error. Missing orders are not assumed cancelled.
Unknown or malformed responses, decreasing fill counts, unconfirmed cancellations,
and position mismatches defer the exit and record `last_error`. An ambiguous
submission that never becomes queryable requires manual broker reconciliation;
it is not automatically assumed absent. Cancellation may already have removed
protection while such an error is unresolved.

There is one active scheduled trade per account/symbol. New scheduled entries
require no existing stock position for that symbol. The bot caps exits at its
tracked fills, but manual trading or corporate actions can invalidate ownership
accounting; use a dedicated paper account for this workflow. Worker and entry
submission exclusion uses a POSIX file lock beside SQLite, supporting processes
on a single host with the same local database, not distributed deployments.

Inspect state without modifying it:

```sql
SELECT id, symbol, status,
       json_extract(state_json, '$.due_at') AS due_at,
       json_extract(state_json, '$.remaining_quantity') AS remaining_quantity,
       json_extract(state_json, '$.last_error') AS last_error
FROM scheduled_stock_exits
ORDER BY updated_at DESC;
```

For unresolved submissions, inspect the saved entry/exit client IDs and broker
order history before making any manual correction. Do not delete a job or clear
its market-order attempts to force a retry: that removes duplicate-sale protection.

Implementation: `app/exit_scheduler.py` (calendar and reconciliation),
`app/stock_execution.py` (strict Webull adapter), and `app/ledger.py` (persistent
jobs). Fake-broker tests cover recovery and cancellation races; actual sandbox
acceptance of GTC bracket exits and account-specific response shapes still needs
verification before enabling the worker.

API references: [stock orders](https://developer.webull.com/apis/docs/trade-api/stock/),
[order detail](https://developer.webull.com/apis/docs/reference/order-detail/),
[cancellation](https://developer.webull.com/apis/docs/reference/common-order-cancel/),
and [positions](https://developer.webull.com/apis/docs/reference/account-position/).

## How main.py works

The runtime flow in [app/main.py](app/main.py) is split into a few clear stages.

### 1. Configuration and environment loading

`Settings` is a `BaseSettings` model that reads environment variables from `.env` and from the process environment. It includes:

- broker and polling settings
- Optionomics credentials and polling controls
- Webull credentials and endpoint
- database path
- risk controls such as `MAX_NOTIONAL_USD`, `ALLOW_SHORT_SELLING`, and `FORCE_REPROCESS`

This lets the app keep runtime settings centralized instead of hardcoding values in the code.

### 2. Data models for valid payloads and trading decisions

The file defines strict models:

- `TradeIdea`: a normalized incoming trade idea from the external feed
- `TradingDecision`: the internal action the bot decides to take
- `OptionomicsTradeIdea`: normalized Optionomics payload used for signal processing

These models enforce common rules such as:

- symbol normalization to uppercase
- direction restrictions like bullish, bearish, or neutral
- level validation for price inputs
- a consistent structure for downstream processing

### 3. Option chain helpers

The app contains helper functions that flatten raw option-chain responses and pick the nearest valid contract:

- `_flatten_option_chain()`
- `select_valid_webull_option_contract()`
- `fetch_webull_option_chain()`

This is important because Webull option contracts are not always returned in a perfectly clean shape. The selector prefers exact matches by expiration and strike, and then falls back to the nearest valid expiration and strike when necessary.

### 4. Polling loop

At app startup, `@app.on_event("startup")` checks whether polling should run:

- `OPTIONOMICS_API_KEY` and `OPTIONOMICS_EMAIL` are present
- `OPTIONOMICS_POLL_ENABLED` is true

If enabled, it launches a daemon thread that loops forever:

```python
while True:
    poll_optionomics_trade_ideas()
    time.sleep(interval_seconds)
```

This means the app does not wait for an external listener; it actively pulls trade ideas on a timer.

### 5. Polling process: from API to decision

`poll_optionomics_trade_ideas()` pulls trade ideas from the Optionomics client and processes each returned item.

For each idea, it does the following:

1. reads `trade_id` and `symbol`
2. checks deduplication in SQLite
3. saves the raw idea to the ledger as queued
4. builds a `TradingDecision` with `build_trade_decision_from_optionomics_payload()`
5. skips invalid or disallowed signals
6. validates symbol matching between payload and decision
7. submits a paper order if the decision passes all gates
8. writes the final status back to SQLite

If the app is in `DRY_RUN=true`, the actual broker call is replaced by a dry-run result object, but the logic still runs end to end.

### 6. Risk gates and decision logic

`build_trade_decision_from_optionomics_payload()` decides whether a trade idea should be:

- `buy`
- `sell_short`
- `skip`

It validates directional logic such as:

- bullish trades need `target > entry` and `stop < entry`
- bearish trades need `target < entry` and `stop > entry`
- neutral trades require positive finite levels with `target < entry < stop` and route to the iron-condor paper submitter

Neutral submission uses four listed contracts, fresh bid/ask quotes, a SELL LIMIT
credit entry, and four-leg BUY exits at 10% profit / 5% loss relative to the entry
limit credit. The selected spread's maximum loss before fees must fit
`MAX_NOTIONAL_USD`. An `events` reservation saves order IDs before sending and
blocks retries after ambiguous submissions. `DRY_RUN=true` remains broker-free.
See [the neutral iron-condor flow](netural-iron-condor.md) for the diagram,
selection rules and sandbox validation limits. `ALLOW_SHORT_SELLING` controls
bearish decisions and is not required for neutral iron condors.

`apply_risk_gates()` provides these additional deterministic guards (the current
polling path does not call it):

- symbol must match
- risk action must be consistent with signal direction
- required price levels must be present
- notional must be positive and capped by `MAX_NOTIONAL_USD`
- shorting is blocked when `ALLOW_SHORT_SELLING=false`

### 7. Order submission flow

`submit_paper_order()` is the broker-facing step.

It:

- creates a client order ID
- exits early in dry-run mode
- resolves a reference price from the payload
- selects an option expiration and strike using the Webull chain
- calls the Webull helper module to place the option order
- returns a response payload with metadata for the ledger

This is where the app turns a validated idea into a real paper-trading action.

### 8. Ledger and deduplication

The `Ledger` class manages SQLite tables:

- `events`: stores processed feed events and their decision/order state
- `optionomics_trade_ideas`: stores each Trade Idea and its final status

The ledger tracks each idea by `trade_id`. These statuses describe application
processing, not the broker's execution or fill status:

| Status | Definition |
| --- | --- |
| `queued` | The idea has been saved for processing, but no final outcome has been recorded yet. It does not mean an order is queued at Webull. A stopped process can leave this status behind. |
| `ordered` | The non-dry-run submission returned successfully and the app recorded the result. This does **not** confirm that the order filled or that the position is closed. Check Webull for execution status. |
| `failed` | An exception interrupted processing or submission. Check application logs and any stored error details. In `main.py`, this alone does **not** prove that Webull rejected or never received the order; a timeout can leave the broker outcome uncertain. |
| `skipped` | The app chose not to submit an order for this processing attempt, for example because price levels were invalid, the symbol did not match, or the market was closed. Inspect the decision rationale or stored skip reason. |
| `dry_run` | The app prepared a simulated order with `DRY_RUN=true`; it did not submit it to Webull. |

The bullish runner uses `submitted` instead of `ordered`, and uses
`submission_unknown` when an exception occurs after its submission callback.
That outcome requires broker reconciliation before retrying. Some bullish skips
(such as duplicate symbols, unavailable quotes, or exceeding the budget) appear
only in scan output and do not create or change a database row. A duplicate skip
does not change an existing `submitted` or `ordered` record to `skipped`.

#### Status transitions

```mermaid
flowchart TD
    A[Trade idea received] --> B{Already processed?}
    B -->|Yes| C[Skip this scan<br/>Keep existing database status]
    B -->|No| Q[queued]

    Q --> V{Validation and submission checks}
    V -->|Not eligible| S[skipped]
    V -->|Exception| F[failed]
    V -->|Eligible| D{DRY_RUN?}

    D -->|Yes| DR[dry_run]
    D -->|No| W[Submit to Webull]

    W -->|Success: main.py| O[ordered]
    W -->|Success: bullish runner| SU[submitted]
    W -->|Exception: main.py| F
    W -->|Exception before submission callback: bullish runner| F
    W -->|Exception after submission callback: bullish runner| U[submission_unknown]
```

`ordered` and `submitted` record successful submission, not a confirmed fill.
`submission_unknown` requires checking Webull before retrying. This diagram
summarizes processing outcomes: the bullish runner performs validation before
claiming a `queued` row, so it can skip an entry without creating a record.

### 9. Health endpoint

The FastAPI app exposes a simple readiness route:

```python
@app.get("/health")
def health() -> dict[str, Any]:
```

This returns a minimal status payload indicating the app is alive and whether dry-run mode is enabled.

## Data flow summary

The end-to-end path looks like this:

```mermaid
graph TD
    A["App startup"] --> B{"OPTIONOMICS_API_KEY and EMAIL present?"}
    B -- "No" --> C["Polling disabled"]
    B -- "Yes" --> D["Start background polling thread"]
    D --> E["Every interval: poll_optionomics_trade_ideas"]

    E --> F["fetch_trade_ideas from Optionomics"]
    F --> G{"Payload valid and trade_id/symbol present?"}
    G -- "No" --> H["Skip malformed payload"]
    G -- "Yes" --> I["Ledger dedupe check"]
    I --> J{"Already processed?"}
    J -- "Yes" --> K["Skip duplicate trade"]
    J -- "No" --> L["Save raw idea to SQLite"]

    L --> M["build_trade_decision_from_optionomics_payload"]
    M --> N{"Decision valid?"}
    N -- "No" --> O["Mark skipped"]
    N -- "Yes" --> P["validate_optionomics_symbol_match"]
    P --> Q{"Symbol matches payload?"}
    Q -- "No" --> O
    Q -- "Yes" --> R["apply_risk_gates"]

    R --> S{"Action allowed?"}
    S -- "No" --> O
    S -- "Yes" --> T["submit_paper_order"]
    T --> U{"DRY_RUN=true?"}
    U -- "Yes" --> V["Return dry-run payload"]
    U -- "No" --> W["Resolve valid Webull contract and submit paper order"]

    V --> X["Ledger mark status: dry_run"]
    W --> Y["Ledger mark status: ordered"]
    O --> Z["Ledger mark status: skipped"]
    H --> AA["Continue polling"]
    K --> AA
    X --> AA
    Y --> AA
    Z --> AA

    AA --> D
```

This flow mirrors the current logic in [app/main.py](app/main.py): fetch, dedupe, normalize, validate, risk-gate, and then either dry-run or submit.

![alt text](image.png)

## Development commands

```bash
PYTHONPATH=. uv run pytest -q
PYTHONPATH=. uv run pytest -q tests/test_apply_risk_gates.py
PYTHONPATH=. uv run python -m compileall app
```

## Notes and safety considerations

- This is a paper-trading project. It is not a full production trade system.
- `DRY_RUN=true` is the safest default for testing and validation.
- Webull option orders are not plain equity market orders; they require a valid option contract and priced entry logic.
- The app is deterministic and rule-based, which helps make behavior easy to inspect in SQLite and logs.
- This project is for educational and research use and is not financial advice.

## Useful queries

```bash
sqlite3 bot.sqlite3 "SELECT trade_id, symbol, status, decision_json FROM optionomics_trade_ideas ORDER BY created_at DESC LIMIT 20;"

sql query
SELECT trade_id, symbol, status, decision_json FROM optionomics_trade_ideas ORDER BY created_at DESC LIMIT 20;
```

```bash
sqlite3 bot.sqlite3 ".schema optionomics_trade_ideas"
```

### removed sql command

```
rm -f bot.sqlite3 bot.sqlite3.exits.lock bot.sqlite3.write.lock && ls -1 bot.sqlite3* 2>/dev/null || true
```

### Browse database records

With the FastAPI app running, open http://127.0.0.1:8000/records or select
“Browse database records” from the dashboard. Choose a ledger table, filter by
created or updated time, and use View to inspect the complete stored record.
Date inputs use your browser timezone; From is inclusive and Until is exclusive.
Today selects the current local day. Results are paginated in groups of 50.
The page is read-only and includes raw stored payloads; use it on localhost.

### Top bullish flow stock brackets

Run the standalone `MainTopBullish` runner to fetch up to 10 bullish-flow symbols,
request current Webull stock snapshots, and prepare one-share limit brackets:

```bash
DRY_RUN=true DATABASE_PATH=/tmp/bullish-preview.sqlite3 uv run python app/main-top-bullish.py --limit 10
```

Requires `OPTIONOMICS_EMAIL`, `OPTIONOMICS_API_KEY`, `WEBULL_APP_KEY`, and
`WEBULL_APP_SECRET` in the environment or `.env`, plus access to Webull snapshot
data. Quotes older than five minutes are skipped, including old quotes outside
market hours. Entry is the quoted stock price, stop is 5% below entry, and target
is 10% above entry. One share must fit `MAX_NOTIONAL_USD` (default $250).

To enable sandbox orders, use `DRY_RUN=false` with your intended `DATABASE_PATH`.
Set `TOP_BULLISH_ACCOUNT_NUMBER` to the desired sandbox account number in `.env`.
The runner requires an exact unique account match before claiming or submitting
a trade. Existing symbol deduplication still applies when changing accounts.
The runner calls `webull-buy-combo-stock.py`; exits use DAY time in force and the
runner does not start the next-day exit scheduler. It scans immediately and
then every five minutes while the process stays running. Stop with Ctrl+C;
add `--once` to run one scan and exit. Scans never overlap, missed intervals
are skipped, and a failed scan is retried at the next scheduled interval.
The feed-only `app/top-bullish.py` remains a single fetch.

Bullish scans require an open regular exchange session, including in dry-run
mode. The XNYS calendar handles weekends, holidays, early closes and DST.
Closed scans return `outside_market_hours` without fetching the feed or quotes;
the scheduler keeps checking every five minutes. Market hours are checked again
before claiming a symbol and immediately before broker submission. If the market
closes after the claim, that symbol is recorded as skipped and remains deduplicated.

The `top_bullish_trades` table is created automatically. It records the flow,
quote, order parameters, broker IDs/response, timestamps and submission status.
A trade ID or symbol already in this table is always skipped, even after a dry
run or failure. Use a separate preview database as above. Deduplication applies
to this runner's table, not other strategies or broker holdings. Ambiguous
submission failures are retained as `submission_unknown` for manual review.

## Configurable exit percentages

Set these in `.env` (values are percentages: `10` means 10%):

```dotenv
BULLISH_PROFIT_PERCENT=10
BULLISH_STOP_LOSS_PERCENT=5
BEARISH_PROFIT_PERCENT=20
BEARISH_STOP_LOSS_PERCENT=10
IRON_CONDOR_PROFIT_PERCENT=10
IRON_CONDOR_STOP_LOSS_PERCENT=5
```

These defaults preserve the existing behavior. Bullish applies to the main stock
submitter, bullish flow runner, and separate option runner's CALL brackets.
Bearish applies to PUT brackets. For long entries, target is entry ×
`(1 + profit / 100)` and stop is entry × `(1 - stop / 100)`. Iron-condor closing
debits are credit × `(1 - profit / 100)` and credit × `(1 + stop / 100)`.
Existing price rounding still applies; percentages use the entry reference/limit,
not a recalculated actual fill.

Values must be finite and positive. Bullish/bearish stop percentages and the
iron-condor profit percentage must be below 100. Invalid values prevent settings
initialization. Process environment values override `.env`. Restart the running
services after changing these values; existing broker orders are not modified.

## Strategy account selection

| Execution path | Account setting |
| --- | --- |
| `main.py` bullish stock branch | `BULLISH_STOCK_ACCOUNT_NUMBER` |
| `main-top-bullish.py` stock runner | `TOP_BULLISH_ACCOUNT_NUMBER` |
| Bearish PUT via `main.py` or `main-option.py` | `OPTIONS_MARGIN_ACCOUNT_NUMBER` |
| Neutral iron condor via either main entry point | `OPTIONS_MARGIN_ACCOUNT_NUMBER` |

These paths share `app.webull_broker.get_account_id(account_number=...)`.
Each trims the configured account number, requires a nonempty value, queries the
broker account list and requires exactly one matching `account_number` with an
API `account_id`. Missing or ambiguous matches stop submission; these paths do
not fall back to the first account. The returned API ID is used in the order.
Dry runs do not perform this account lookup.

Set both variables to the same intended account number to use one account for
all three paths. They remain separate settings so they can also select different
accounts. The local configuration was compared and the two values matched on
2026-09-16; actual account identifiers remain only in local `.env`. No live
account lookup or account-type/permission verification was performed.

The main service's bullish stock branch uses its own required
`BULLISH_STOCK_ACCOUNT_NUMBER`, with the same exact-match lookup and no fallback.
The separate `main-option.py` CALL path still selects the first returned account.
See [bullish-stock.md](bullish-stock.md) for the main-service diagram.

Process environment overrides `.env`. Restart the affected services after an
account change. Existing orders and ledger reservations are not moved or reset.
The separate strategies do not share a complete position/deduplication guard.

For the dedicated bullish runner’s end-to-end diagram, account selection,
configurable exits and ledger behavior, see [bullish.md](bullish.md).
