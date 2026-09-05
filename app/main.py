from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from enum import Enum
import sys

from app.optionomics_client import fetch_trade_ideas
from app.optionomics import build_trade_decision_from_optionomics_payload
from app.webull_submitter import submit_paper_order, _is_webull_rate_limit_error as is_webull_rate_limit_error, _load_webull_combo_module as _load_webull_combo_module_impl
from app.ledger import Ledger

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("optionomics_bot")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

# Keep the app at INFO while suppressing noisy background watchers and debug logs.
logging.getLogger().setLevel(logging.INFO)
for _name in ("debugpy", "pydevd", "asyncio", "urllib3", "webull", "uvicorn.access", "uvicorn.error", "watchfiles", "watchfiles.main"):
    logging.getLogger(_name).setLevel(logging.WARNING if _name.startswith("watchfiles") else logging.INFO)


def color_symbol(symbol: str | None) -> str:
    value = str(symbol or "unknown").upper()
    return f"\033[32m{value}\033[0m"


def color_error(value: str | None) -> str:
    text = str(value or "unknown").upper()
    return f"\033[31m{text}\033[0m"


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""
    dry_run: bool = Field(default=True, alias="DRY_RUN")
    max_notional_usd: float = Field(default=250.0, gt=0.0, alias="MAX_NOTIONAL_USD")
    allow_short_selling: bool = Field(default=False, alias="ALLOW_SHORT_SELLING")
    force_reprocess: bool = Field(default=False, alias="FORCE_REPROCESS")
    optionomics_api_key: str | None = Field(default=None, alias="OPTIONOMICS_API_KEY")
    optionomics_email: str | None = Field(default=None, alias="OPTIONOMICS_EMAIL")
    optionomics_api_url: str = Field(default="https://optionomics.ai/api/v1/trade_ideas", alias="OPTIONOMICS_API_URL")
    optionomics_poll_enabled: bool = Field(default=True, alias="OPTIONOMICS_POLL_ENABLED")
    optionomics_poll_interval_seconds: int = Field(default=600, ge=1, alias="OPTIONOMICS_POLL_INTERVAL_SECONDS")

    webull_app_key: str | None = Field(default=None, alias="WEBULL_APP_KEY")
    webull_app_secret: str | None = Field(default=None, alias="WEBULL_APP_SECRET")
    webull_endpoint: str = Field(default="api.sandbox.webull.com", alias="WEBULL_ENDPOINT")

    database_path: str = Field(default="bot.sqlite3", alias="DATABASE_PATH")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"


class TimeInForce(Enum):
    DAY = "DAY"


class OrderClass(Enum):
    SIMPLE = "SIMPLE"


class TradeIdea(BaseModel):
    """Validated Optionomics Trade Idea payload."""

    alert_name: str
    source: Literal["trade_idea"]
    symbol: str
    direction: Literal["bullish", "bearish", "neutral"]
    strategy: str
    model: str | None = None
    entry_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    triggered_at: datetime
    matched_criteria: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
            raise ValueError("symbol must be a valid ticker")

        return symbol


class TradingDecision(BaseModel):
    """Structured trade decision used by deterministic risk gates."""

    action: Literal["skip", "buy", "sell_short"]
    symbol: str
    strategy: str = "placeholder"
    notional_usd: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    risk_notes: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()





def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

def resolve_option_contract_symbol(
    symbol: str,
    *,
    direction: Literal["bullish", "bearish", "neutral"] = "bullish",
    price_reference: float | None = None,
    expiration_days: int = 30,
    expiration_date: str | None = None,
) -> str:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise ValueError("Option contract symbol cannot be empty")

    reference = float(price_reference) if price_reference is not None else 100.0
    option_type = "C" if direction in {"bullish", "neutral"} else "P"
    strike = round(reference / 5.0) * 5.0
    strike_code = f"{int(round(strike * 1000)):08d}"
    expiration = (datetime.now(UTC) + timedelta(days=max(expiration_days, 1))).strftime("%y%m%d")
    return f"{ticker}{expiration}{option_type}{strike_code}"




app = FastAPI(title="Optionomics Trade Ideas Trading Bot", version="1.0.0")


def poll_optionomics_trade_ideas() -> list[dict[str, Any]]:
    settings = get_settings()
    email = os.getenv("OPTIONOMICS_EMAIL") or "you@example.com"
    try:
        ideas = fetch_trade_ideas(email, timeout=30)
    except RuntimeError as exc:
        logger.exception("Unable to fetch Optionomics trade ideas: %s", exc)
        return []

    ledger = Ledger(settings.database_path)

    for idea in ideas:
        try:
            #logger.info("Optionomics raw idea payload: %s", idea)
            trade_id = idea.get("id")
            symbol = idea.get("symbol")
            logger.info("Processing Optionomics idea: trade_id=%s symbol=%s", trade_id, color_symbol(symbol))
            if trade_id is None and symbol is None:
                #logger.warning("Skipping invalid Optionomics payload with no id or symbol: %s", idea)
                continue

            trade_id = str(trade_id if trade_id is not None else symbol or "unknown")
            if settings.force_reprocess:
                logger.warning("FORCE_REPROCESS=true; bypassing dedupe for Optionomics trade: trade_id=%s symbol=%s", trade_id, color_symbol(symbol))
            elif ledger.is_trade_idea_seen(trade_id):
                logger.info("Skipping already-processed Optionomics trade: trade_id=%s symbol=%s", trade_id, color_symbol(symbol))
                continue

            ledger.save_trade_idea(trade_id, idea, status="queued")
            decision_data = build_trade_decision_from_optionomics_payload(idea, settings)
            decision = TradingDecision(**decision_data)
            if decision.action == "skip":
                ledger.mark_trade_idea_status(trade_id, status="skipped", decision=decision)
                logger.info("Skipping %s from Optionomics: %s", decision.symbol, decision.rationale)
                continue

            if not validate_optionomics_symbol_match(idea, decision):
                ledger.mark_trade_idea_status(trade_id, status="skipped", decision=decision)
                logger.warning(
                    "Skipping Optionomics trade %s because decision symbol %s does not match payload symbol %s",
                    trade_id,
                    color_error(decision.symbol),
                    color_error(str(idea.get("symbol") or "").upper()),
                )
                continue

            payload = TradeIdea(
                alert_name=str(idea.get("id") or idea.get("symbol") or "optionomics"),
                source="trade_idea",
                symbol=decision.symbol,
                direction=idea.get("direction", "neutral"),
                strategy=idea.get("strategy") or "optionomics",
                entry_price=idea.get("levels", {}).get("entry"),
                target_price=idea.get("levels", {}).get("target"),
                stop_price=idea.get("levels", {}).get("stop"),
                triggered_at=datetime.now(UTC),
                matched_criteria={"pipeline_short_name": idea.get("pipeline_short_name"), "pipeline_name": idea.get("pipeline_name")},
            )
            order_payload = maybe_submit_order(
                ledger,
                decision,
                settings,
                payload,
                trade_id=trade_id,
            )
            if order_payload is None:
                continue

            logger.info("Optionomics %s decision: %s -> %s", decision.symbol, decision.strategy, order_payload)
        except Exception as exc:
            ledger.mark_trade_idea_status(
                trade_id,
                status="failed",
                decision=TradingDecision(**build_trade_decision_from_optionomics_payload(idea, settings)) if "direction" in idea else None,
            )
            if is_webull_rate_limit_error(exc):
                logger.warning(
                    "Webull rate limit hit while processing Optionomics trade %s; trade marked failed and will be retried on the next poll: %s",
                    trade_id,
                    exc,
                )
            else:
                logger.exception("Failed to process Optionomics idea: %s", color_error(str(idea)))
                logger.warning("Marked Optionomics trade %s as failed because broker submission raised: %s", trade_id, exc)

    return ideas


@app.on_event("startup")
def start_optionomics_polling() -> None:
    settings = get_settings()
    should_poll = bool(os.getenv("OPTIONOMICS_API_KEY") and os.getenv("OPTIONOMICS_EMAIL")) and settings.optionomics_poll_enabled
    if not should_poll:
        reason = "disabled by OPTIONOMICS_POLL_ENABLED" if not settings.optionomics_poll_enabled else "missing OPTIONOMICS_API_KEY or OPTIONOMICS_EMAIL"
        logger.info("Optionomics polling disabled: %s", reason)
        return

    interval_seconds = settings.optionomics_poll_interval_seconds
    logger.info("Starting Optionomics polling every %s seconds", interval_seconds)

    def runner() -> None:
        while True:
            try:
                poll_optionomics_trade_ideas()
            except Exception:
                logger.exception("Polling loop crashed")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def maybe_submit_order(
    ledger: Ledger,
    decision: TradingDecision,
    settings: Settings,
    payload: TradeIdea,
    *,
    trade_id: str | None = None,
) -> dict[str, Any] | None:
    # The polling loop performs the duplicate check before the trade is inserted
    # into the ledger. This helper should submit the current trade without
    # falsely treating the just-inserted row as a duplicate.
    if trade_id is not None and ledger.has_ordered_trade(trace_id=trade_id, symbol=decision.symbol):
        logger.info(
            "Skipping Webull submission for already-ordered trade_id=%s symbol=%s",
            trade_id,
            decision.symbol,
        )
        return None

    order_payload = submit_paper_order(decision, settings, fingerprint_for(payload), payload)
    if trade_id is not None:
        status = "dry_run" if settings.dry_run else "ordered"
        ledger.mark_trade_idea_status(trade_id, status=status, decision=decision, order_payload=order_payload)
    return order_payload


def fingerprint_for(payload: TradeIdea) -> str:
    material = "|".join(
        [
            payload.alert_name,
            payload.source,
            payload.symbol,
            payload.direction,
            payload.strategy,
            payload.triggered_at.isoformat(),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_optionomics_symbol_match(payload: dict[str, Any], decision: TradingDecision) -> bool:
    payload_symbol = str(payload.get("symbol") or "").strip().upper()
    decision_symbol = str(decision.symbol or "").strip().upper()
    if not payload_symbol or not decision_symbol:
        return False
    return payload_symbol == decision_symbol


def build_trade_decision_from_optionomics_payload(payload: dict[str, Any], settings: Settings) -> TradingDecision:
    # compatibility wrapper: optionomics.build_trade_decision_from_optionomics_payload returns a dict
    from app.optionomics import build_trade_decision_from_optionomics_payload as _builder

    result = _builder(payload, settings)
    if isinstance(result, dict):
        return TradingDecision(**result)
    return result


def _load_webull_combo_module() -> Any:
    # expose loader for tests/monkeypatching but delegate to webull_submitter implementation
    return _load_webull_combo_module_impl()


def build_option_trade_request(
    decision: TradingDecision,
    *,
    direction: Literal["bullish", "bearish", "neutral"] = "bullish",
    notional_usd: float | None = None,
    client_order_id: str | None = None,
    payload: dict[str, Any] | None = None,
):
    payload = payload or {}
    contract_symbol = resolve_option_contract_symbol(
        decision.symbol,
        direction=direction,
        price_reference=float(payload.get("levels", {}).get("entry") or payload.get("entry_price") or max(decision.notional_usd, 1.0) / 10.0),
    )

    requested_notional = round(float(notional_usd if notional_usd is not None else decision.notional_usd), 2)
    return SimpleNamespace(
        symbol=contract_symbol,
        notional=requested_notional,
        side=OrderSide.BUY if decision.action == "buy" else OrderSide.SELL,
        type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id or f"option-{decision.symbol.lower()}-{int(time.time())}",
        order_class=OrderClass.SIMPLE,
    )


def build_trade_decision(payload: TradeIdea, settings: Settings) -> TradingDecision:
    action = "skip"
    rationale = "No trade action determined."
    risk_notes: list[str] = []

    if payload.direction == "bullish":
        action = "buy"
        rationale = "Execute bullish trade idea as a paper equity buy."
    elif payload.direction == "bearish":
        if settings.allow_short_selling:
            action = "sell_short"
            rationale = "Execute bearish trade idea as a paper equity short sale."
        else:
            rationale = "Short selling disabled; skipping bearish trade idea."
            risk_notes.append("Short selling disabled")
    else:
        rationale = "Neutral trade idea; skipping execution."
        risk_notes.append("Neutral trade idea")

    if action != "skip":
        if payload.entry_price is None or payload.target_price is None or payload.stop_price is None:
            action = "skip"
            rationale = "Missing entry, target, or stop levels."
            risk_notes.append("Missing price levels")
        elif action == "buy":
            if payload.target_price <= payload.entry_price or payload.stop_price >= payload.entry_price:
                action = "skip"
                rationale = "Bullish trade idea has invalid price levels."
                risk_notes.append("Bullish price levels invalid")
        elif action == "sell_short":
            if payload.target_price >= payload.entry_price or payload.stop_price <= payload.entry_price:
                action = "skip"
                rationale = "Bearish trade idea has invalid price levels."
                risk_notes.append("Bearish price levels invalid")

    notional = settings.max_notional_usd if action != "skip" else 0.0
    return TradingDecision(
        action=action,
        symbol=payload.symbol,
        notional_usd=round(notional, 2),
        confidence=1.0,
        rationale=rationale,
        risk_notes=risk_notes,
    )



def skip_decision(decision: TradingDecision, reason: str) -> TradingDecision:
    notes = [*decision.risk_notes, reason]
    rationale = f"{decision.rationale} Risk gate: {reason}"
    return decision.model_copy(
        update={
            "action": "skip",
            "notional_usd": 0.0,
            "rationale": rationale,
            "risk_notes": notes,
        }
    )


def apply_risk_gates(
    payload: TradeIdea,
    decision: TradingDecision,
    settings: Settings,
) -> TradingDecision:
    if decision.symbol != payload.symbol:
        return skip_decision(decision, "Decision symbol does not match payload symbol")

    if decision.action == "skip":
        return decision.model_copy(update={"notional_usd": 0.0})

    if payload.entry_price is None or payload.target_price is None or payload.stop_price is None:
        return skip_decision(decision, "Entry, target, and stop levels are required")

    if decision.action == "buy":
        if payload.direction != "bullish":
            return skip_decision(decision, "Buy action is allowed only for bullish trade ideas")
        if payload.target_price <= payload.entry_price:
            return skip_decision(decision, "Bullish target must be above entry")
        if payload.stop_price >= payload.entry_price:
            return skip_decision(decision, "Bullish stop must be below entry")

    if decision.action == "sell_short":
        if not settings.allow_short_selling:
            return skip_decision(decision, "Short selling is disabled")
        if payload.direction != "bearish":
            return skip_decision(decision, "Short action is allowed only for bearish trade ideas")
        if payload.target_price >= payload.entry_price:
            return skip_decision(decision, "Bearish target must be below entry")
        if payload.stop_price <= payload.entry_price:
            return skip_decision(decision, "Bearish stop must be above entry")

    if decision.notional_usd <= 0:
        return skip_decision(decision, "Order notional must be positive")

    notional = min(decision.notional_usd, settings.max_notional_usd)
    return decision.model_copy(update={"notional_usd": round(notional, 2)})


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
    }
