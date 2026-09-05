from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class OptionomicsTradeIdea(BaseModel):
    id: str | None = None
    symbol: str
    direction: str
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


def normalize_confidence(value: Any, *, default: float = 1.0) -> float:
    try:
        numeric = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default

    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return default

    return round(max(0.0, min(1.0, numeric)), 4)


def validate_optionomics_directional_levels(direction: str, entry: float, target: float, stop: float) -> bool:
    if direction == "bullish":
        return target > entry and stop < entry
    if direction == "bearish":
        return target < entry and stop > entry
    if direction == "neutral":
        return target < entry and stop > entry
    return False


def build_trade_decision_from_optionomics_payload(payload: dict[str, Any], settings) -> dict[str, Any]:
    idea = OptionomicsTradeIdea.model_validate(payload)
    levels = idea.levels
    pipeline = (idea.pipeline_short_name or idea.pipeline_name or "placeholder").strip()
    strategy_name = "iron_condor" if (idea.direction == "neutral" or pipeline.lower() == "crush") else "placeholder"

    normalized_confidence = normalize_confidence(idea.confidence_score, default=1.0)

    if idea.direction == "bullish":
        action = "buy"
        rationale = f"Optionomics {pipeline} idea: bullish setup flagged for {strategy_name} execution."
    elif idea.direction == "bearish":
        if settings.allow_short_selling:
            action = "sell_short"
            rationale = f"Optionomics {pipeline} idea: bearish setup flagged for {strategy_name} execution."
        else:
            return {
                "action": "skip",
                "symbol": idea.symbol,
                "strategy": strategy_name,
                "notional_usd": 0.0,
                "confidence": normalized_confidence,
                "rationale": "Short selling disabled; skipping bearish Optionomics trade idea.",
                "risk_notes": ["Short selling disabled"],
            }
    else:
        action = "buy"
        rationale = f"Optionomics {pipeline} idea: neutral setup selected for iron_condor strategy execution."

    entry = levels.get("entry")
    target = levels.get("target")
    stop = levels.get("stop")
    current = levels.get("current")
    peak = levels.get("peak")

    if entry is None or target is None or stop is None:
        return {
            "action": "skip",
            "symbol": idea.symbol,
            "strategy": strategy_name,
            "notional_usd": 0.0,
            "confidence": normalized_confidence,
            "rationale": "Optionomics levels missing required entry, target, and stop values.",
            "risk_notes": ["Missing price levels"],
        }

    if action == "buy":
        if not validate_optionomics_directional_levels(idea.direction, entry, target, stop):
            message = (
                "Neutral Optionomics iron-condor idea has invalid price levels."
                if idea.direction == "neutral"
                else "Bullish Optionomics idea has invalid price levels."
            )
            return {
                "action": "skip",
                "symbol": idea.symbol,
                "strategy": strategy_name,
                "notional_usd": 0.0,
                "confidence": normalized_confidence,
                "rationale": message,
                "risk_notes": ["Neutral iron-condor price levels invalid" if idea.direction == "neutral" else "Bullish price levels invalid"],
            }
    else:
        if not validate_optionomics_directional_levels(idea.direction, entry, target, stop):
            return {
                "action": "skip",
                "symbol": idea.symbol,
                "strategy": strategy_name,
                "notional_usd": 0.0,
                "confidence": normalized_confidence,
                "rationale": "Bearish Optionomics idea has invalid price levels.",
                "risk_notes": ["Bearish price levels invalid"],
            }

    notes = [f"Pipeline={pipeline}", f"Entry={entry}", f"Target={target}", f"Stop={stop}", f"Current={current}", f"Peak={peak}"]
    notional = settings.max_notional_usd
    return {
        "action": action,
        "symbol": idea.symbol,
        "strategy": strategy_name,
        "notional_usd": round(notional, 2),
        "confidence": normalized_confidence,
        "rationale": f"{rationale} Strategy selected: {strategy_name}. Levels: {', '.join(notes)}",
        "risk_notes": notes,
    }
