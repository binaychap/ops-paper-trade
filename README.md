# Optionomics Polling Paper Trading Bot

This project polls the Optionomics trade-ideas API, validates each idea, applies deterministic risk gates, deduplicates by trade ID, and then submits a paper order through Alpaca when the trade passes validation.

The current implementation is polling-based rather than webhook-based.

## What the bot does

- fetches trade ideas from the configured Optionomics endpoint on startup
- keeps a background polling loop alive while the app runs
- validates the payload shape and required fields
- skips invalid or duplicate trade IDs
- maps the Optionomics signal into a trading decision
- applies risk gates such as max notional, direction checks, and price-level validation
- submits a dry-run response or real paper order to Alpaca
- stores the decision and order payload in SQLite for later review

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Optionomics account with API access and a valid email + API key
- Alpaca paper-trading account, or keep `DRY_RUN=true` while testing

## Setup

```bash
cp .env.example .env
```

Then fill in your values in the `.env` file:

```env
WEBHOOK_SECRET=replace-with-a-long-random-secret

DRY_RUN=true
MAX_NOTIONAL_USD=250
ALLOW_SHORT_SELLING=false

OPTIONOMICS_API_KEY=your-optionomics-api-key
OPTIONOMICS_EMAIL=you@example.com
OPTIONOMICS_API_URL=https://optionomics.ai/api/v1/trade_ideas
OPTIONOMICS_POLL_ENABLED=true
OPTIONOMICS_POLL_INTERVAL_SECONDS=600

ALPACA_API_KEY=your-alpaca-paper-key
ALPACA_SECRET_KEY=your-alpaca-paper-secret
ALPACA_PAPER=true

DATABASE_PATH=bot.sqlite3
```

## Run locally

```bash
uv run fastapi dev app/main.py
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Runtime behavior

On app startup, the bot checks whether polling is enabled and whether the required Optionomics credentials are present. If enabled, it starts a background thread that calls the fetcher repeatedly using the configured interval.

The loop does this:

1. fetches trade ideas from Optionomics
2. validates the response and skips empty or malformed payloads
3. builds a trading decision from the payload
4. rejects trades that fail the risk gates
5. ensures the payload symbol matches the decision symbol before execution
6. submits a dry run or real paper order
7. writes the final status and order payload to SQLite

## Development

```bash
PYTHONPATH=. uv run pytest -q
uv run python -m compileall app
```

## Important notes

- Paper trading is the supported mode for this project.
- `DRY_RUN=true` suppresses broker submission while still exercising the decision pipeline.
- Trades are deduplicated by `trade_id` before a real order is submitted.
- This bot is a reference implementation and not financial advice.

## Run test

### PYTHONPATH=. uv run pytest -q tests/test_apply_risk_gates.py

### PYTHONPATH=. uv run pytest -q

- sqlite3 bot.sqlite3 "SELECT trade_id, symbol, status, decision_json FROM optionomics_trade_ideas ORDER BY created_at DESC LIMIT 20;"
