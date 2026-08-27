from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.optionomics_client import fetch_trade_ideas

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    webhook_secret: str | None = Field(default=None, alias="WEBHOOK_SECRET")
    dry_run: bool = Field(default=True, alias="DRY_RUN")
    max_notional_usd: float = Field(default=250.0, gt=0.0, alias="MAX_NOTIONAL_USD")
    allow_short_selling: bool = Field(default=False, alias="ALLOW_SHORT_SELLING")
    force_reprocess: bool = Field(default=False, alias="FORCE_REPROCESS")
    optionomics_api_key: str | None = Field(default=None, alias="OPTIONOMICS_API_KEY")
    optionomics_email: str | None = Field(default=None, alias="OPTIONOMICS_EMAIL")
    optionomics_api_url: str = Field(default="https://optionomics.ai/api/v1/trade_ideas", alias="OPTIONOMICS_API_URL")
    optionomics_poll_enabled: bool = Field(default=True, alias="OPTIONOMICS_POLL_ENABLED")
    optionomics_poll_interval_seconds: int = Field(default=600, ge=1, alias="OPTIONOMICS_POLL_INTERVAL_SECONDS")

    alpaca_api_key: str | None = Field(default=None, alias="ALPACA_API_KEY")
    alpaca_secret_key: str | None = Field(default=None, alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, alias="ALPACA_PAPER")

    database_path: str = Field(default="bot.sqlite3", alias="DATABASE_PATH")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


class TradeIdeaWebhook(BaseModel):
    """Validated Optionomics Trade Idea webhook payload."""

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


class OptionomicsTradeIdea(BaseModel):
    """Normalized Optionomics trade-idea payload consumed from the API."""

    id: str | None = None
    symbol: str
    direction: Literal["bullish", "bearish", "neutral"]
    strategy: str | None = None
    pipeline_short_name: str | None = None
    pipeline_name: str | None = None
    levels: dict[str, float] = Field(default_factory=dict)
    generated_at: datetime | None = None
    confidence_score: float | None = None
    thesis: str | None = None
    status: str | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
            raise ValueError("symbol must be a valid ticker")
        return symbol

    @field_validator("levels")
    @classmethod
    def normalize_levels(cls, value: dict[str, Any]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for key, raw in value.items():
            try:
                normalized[key] = float(raw)
            except (TypeError, ValueError):
                continue
        return normalized


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Ledger:
    """Small SQLite ledger for received webhooks, decisions, and orders."""

    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_events (
                    fingerprint TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    decision_json TEXT,
                    order_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS optionomics_trade_ideas (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    strategy TEXT,
                    pipeline_name TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision_json TEXT,
                    order_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_trade_idea(self, trade_id: str, payload: dict[str, Any], *, status: str = "queued") -> None:
        now = utc_now_iso()
        payload_json = json.dumps(payload, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO optionomics_trade_ideas
                    (trade_id, symbol, direction, strategy, pipeline_name, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    str(payload.get("symbol") or ""),
                    str(payload.get("direction") or "neutral"),
                    str(payload.get("strategy") or ""),
                    str(payload.get("pipeline_name") or payload.get("pipeline_short_name") or ""),
                    payload_json,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()

    def is_trade_idea_seen(self, trade_id: str) -> bool:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            row = conn.execute(
                "SELECT 1 FROM optionomics_trade_ideas WHERE trade_id = ? LIMIT 1",
                (trade_id,),
            ).fetchone()
        return row is not None

    def mark_trade_idea_status(self, trade_id: str, *, status: str, decision: TradingDecision | None = None, order_payload: dict[str, Any] | None = None) -> None:
        now = utc_now_iso()
        decision_json = json.dumps(decision.model_dump(mode="json"), sort_keys=True) if decision else None
        order_json = json.dumps(order_payload or {}, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                UPDATE optionomics_trade_ideas
                   SET status = ?, decision_json = ?, order_json = ?, updated_at = ?
                 WHERE trade_id = ?
                """,
                (status, decision_json, order_json, now, trade_id),
            )
            conn.commit()

    def reserve(self, fingerprint: str, payload: TradeIdeaWebhook) -> bool:
        now = utc_now_iso()
        payload_json = json.dumps(payload.model_dump(mode="json"), sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO webhook_events
                    (fingerprint, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fingerprint, "queued", payload_json, now, now),
            )
            conn.commit()

        return cursor.rowcount == 1

    def finish(
        self,
        fingerprint: str,
        status: str,
        decision: TradingDecision,
        order_payload: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now_iso()
        decision_json = json.dumps(decision.model_dump(mode="json"), sort_keys=True)
        order_json = json.dumps(order_payload or {}, sort_keys=True)

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                UPDATE webhook_events
                   SET status = ?, decision_json = ?, order_json = ?, error = NULL, updated_at = ?
                 WHERE fingerprint = ?
                """,
                (status, decision_json, order_json, now, fingerprint),
            )
            conn.commit()

    def fail(self, fingerprint: str, error: str) -> None:
        now = utc_now_iso()

        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.execute(
                """
                UPDATE webhook_events
                   SET status = ?, error = ?, updated_at = ?
                 WHERE fingerprint = ?
                """,
                ("failed", error[:1000], now, fingerprint),
            )
            conn.commit()


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
            logger.info("Processing Optionomics idea: trade_id=%s symbol=%s", trade_id, symbol)
            if trade_id is None and symbol is None:
                #logger.warning("Skipping invalid Optionomics payload with no id or symbol: %s", idea)
                continue

            trade_id = str(trade_id if trade_id is not None else symbol or "unknown")
            if settings.force_reprocess:
                logger.warning("FORCE_REPROCESS=true; bypassing dedupe for Optionomics trade %s (%s)", trade_id, symbol)
            elif ledger.is_trade_idea_seen(trade_id):
                logger.info("Skipping already-processed Optionomics trade %s (%s)", trade_id, symbol)
                continue

            ledger.save_trade_idea(trade_id, idea, status="queued")
            decision = build_trade_decision_from_optionomics_payload(idea, settings)
            if decision.action == "skip":
                ledger.mark_trade_idea_status(trade_id, status="skipped", decision=decision)
                logger.info("Skipping %s from Optionomics: %s", decision.symbol, decision.rationale)
                continue

            if not validate_optionomics_symbol_match(idea, decision):
                ledger.mark_trade_idea_status(trade_id, status="skipped", decision=decision)
                logger.warning(
                    "Skipping Optionomics trade %s because decision symbol %s does not match payload symbol %s",
                    trade_id,
                    decision.symbol,
                    str(idea.get("symbol") or "").upper(),
                )
                continue

            order_payload = submit_paper_order(decision, settings, fingerprint_for(TradeIdeaWebhook(
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
            )))
            status = "dry_run" if settings.dry_run else "ordered"
            ledger.mark_trade_idea_status(trade_id, status=status, decision=decision, order_payload=order_payload)
            logger.info("Optionomics %s decision: %s -> %s", decision.symbol, decision.strategy, order_payload)
        except Exception:
            logger.exception("Failed to process Optionomics idea: %s", idea)

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


def fingerprint_for(payload: TradeIdeaWebhook) -> str:
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


def build_trade_decision(payload: TradeIdeaWebhook, settings: Settings) -> TradingDecision:
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


def validate_optionomics_directional_levels(direction: Literal["bullish", "bearish", "neutral"], entry: float, target: float, stop: float) -> bool:
    if direction == "bullish":
        return target > entry and stop < entry
    if direction == "bearish":
        return target < entry and stop > entry
    if direction == "neutral":
        return target < entry and stop > entry
    return False


def build_trade_decision_from_optionomics_payload(payload: dict[str, Any], settings: Settings) -> TradingDecision:
    idea = OptionomicsTradeIdea.model_validate(payload)
    levels = idea.levels
    pipeline = (idea.pipeline_short_name or idea.pipeline_name or "placeholder").strip()
    strategy_name = "iron_condor" if (idea.direction == "neutral" or pipeline.lower() == "crush") else "placeholder"

    if idea.direction == "bullish":
        action: Literal["skip", "buy", "sell_short"] = "buy"
        rationale = f"Optionomics {pipeline} idea: bullish setup flagged for {strategy_name} execution."
    elif idea.direction == "bearish":
        if settings.allow_short_selling:
            action = "sell_short"
            rationale = f"Optionomics {pipeline} idea: bearish setup flagged for {strategy_name} execution."
        else:
            action = "skip"
            rationale = "Short selling disabled; skipping bearish Optionomics trade idea."
            return TradingDecision(
                action=action,
                symbol=idea.symbol,
                strategy=strategy_name,
                notional_usd=0.0,
                confidence=idea.confidence_score if idea.confidence_score is not None else 0.0,
                rationale=rationale,
                risk_notes=["Short selling disabled"],
            )
    else:
        action = "buy"
        rationale = f"Optionomics {pipeline} idea: neutral setup selected for iron_condor strategy execution."

    if action == "buy" and (idea.direction == "neutral" or pipeline.lower() == "crush"):
        logger.info("Optionomics signal selected strategy=%s for symbol=%s", strategy_name, idea.symbol)

    entry = levels.get("entry")
    target = levels.get("target")
    stop = levels.get("stop")
    current = levels.get("current")
    peak = levels.get("peak")

    if entry is None or target is None or stop is None:
        return TradingDecision(
            action="skip",
            symbol=idea.symbol,
            strategy=strategy_name,
            notional_usd=0.0,
            confidence=idea.confidence_score if idea.confidence_score is not None else 0.0,
            rationale="Optionomics levels missing required entry, target, and stop values.",
            risk_notes=["Missing price levels"],
        )

    if action == "buy":
        if not validate_optionomics_directional_levels(idea.direction, entry, target, stop):
            message = (
                "Neutral Optionomics iron-condor idea has invalid price levels."
                if idea.direction == "neutral"
                else "Bullish Optionomics idea has invalid price levels."
            )
            return TradingDecision(
                action="skip",
                symbol=idea.symbol,
                strategy=strategy_name,
                notional_usd=0.0,
                confidence=idea.confidence_score if idea.confidence_score is not None else 0.0,
                rationale=message,
                risk_notes=["Neutral iron-condor price levels invalid" if idea.direction == "neutral" else "Bullish price levels invalid"],
            )
    else:
        if not validate_optionomics_directional_levels(idea.direction, entry, target, stop):
            return TradingDecision(
                action="skip",
                symbol=idea.symbol,
                strategy=strategy_name,
                notional_usd=0.0,
                confidence=idea.confidence_score if idea.confidence_score is not None else 0.0,
                rationale="Bearish Optionomics idea has invalid price levels.",
                risk_notes=["Bearish price levels invalid"],
            )

    notes = [f"Pipeline={pipeline}", f"Entry={entry}", f"Target={target}", f"Stop={stop}", f"Current={current}", f"Peak={peak}"]
    notional = settings.max_notional_usd
    return TradingDecision(
        action=action,
        symbol=idea.symbol,
        strategy=strategy_name,
        notional_usd=round(notional, 2),
        confidence=min(max(idea.confidence_score if idea.confidence_score is not None else 1.0, 0.0), 1.0),
        rationale=f"{rationale} Strategy selected: {strategy_name}. Levels: {', '.join(notes)}",
        risk_notes=notes,
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
    payload: TradeIdeaWebhook,
    decision: TradingDecision,
    settings: Settings,
) -> TradingDecision:
    if decision.symbol != payload.symbol:
        return skip_decision(decision, "Decision symbol does not match webhook symbol")

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


def submit_paper_order(
    decision: TradingDecision,
    settings: Settings,
    fingerprint: str,
) -> dict[str, Any]:
    client_order_id = f"om-{fingerprint[:24]}"
    logger.info("Processing paper order for %s", decision.symbol)
    if settings.dry_run:
        logger.info("DRY_RUN=true; broker order suppressed for %s", decision.symbol)
        return {
            "dry_run": True,
            "client_order_id": client_order_id,
            "symbol": decision.symbol,
            "action": decision.action,
            "notional_usd": decision.notional_usd,
        }

    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise RuntimeError("Alpaca credentials are required when DRY_RUN=false")

    if not settings.alpaca_paper:
        raise RuntimeError("This tutorial bot supports paper trading only; set ALPACA_PAPER=true")
    logger.info("Submitting paper order for %s via Alpaca [client_order_id=%s]", decision.symbol, client_order_id)
    trading_client = TradingClient(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        paper=settings.alpaca_paper,
    )

    side = OrderSide.BUY if decision.action == "buy" else OrderSide.SELL
    order_data = MarketOrderRequest(
        symbol=decision.symbol,
        notional=round(decision.notional_usd, 2),
        side=side,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
    )

    order = trading_client.submit_order(order_data=order_data)
    logger.info(
        "Alpaca order submitted successfully: symbol=%s side=%s notional_usd=%s order_id=%s status=%s",
        getattr(order, "symbol", decision.symbol),
        getattr(order, "side", side),
        decision.notional_usd,
        getattr(order, "id", ""),
        getattr(order, "status", "unknown"),
    )

    return {
        "dry_run": False,
        "id": str(getattr(order, "id", "")),
        "client_order_id": getattr(order, "client_order_id", client_order_id),
        "symbol": getattr(order, "symbol", decision.symbol),
        "status": str(getattr(order, "status", "unknown")),
        "side": str(getattr(order, "side", side)),
        "notional_usd": decision.notional_usd,
    }


def process_trade_idea(payload: TradeIdeaWebhook, fingerprint: str) -> None:
    settings = get_settings()
    ledger = Ledger(settings.database_path)

    try:
        decision = build_trade_decision(payload, settings)
        gated_decision = apply_risk_gates(payload, decision, settings)

        if gated_decision.action == "skip":
            ledger.finish(fingerprint, "skipped", gated_decision)
            logger.info("Skipped %s: %s", payload.symbol, gated_decision.rationale)
            return

        order_payload = submit_paper_order(gated_decision, settings, fingerprint)
        final_status = "dry_run" if settings.dry_run else "ordered"
        ledger.finish(fingerprint, final_status, gated_decision, order_payload)
        logger.info("Processed %s with status %s", payload.symbol, final_status)
    except Exception as exc:
        ledger.fail(fingerprint, str(exc))
        logger.exception("Failed to process webhook %s", fingerprint)
        raise


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
        "alpaca_paper": settings.alpaca_paper,
    }
