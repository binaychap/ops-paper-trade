from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.main import (
    TradeIdea,
    TradingDecision,
    apply_risk_gates,
    build_trade_decision,
    select_valid_webull_option_contract,
    validate_optionomics_symbol_match,
)


def make_payload(symbol: str = "AAPL", direction: str = "bullish") -> TradeIdea:
    return TradeIdea(
        alert_name="test",
        source="trade_idea",
        symbol=symbol,
        direction=direction,
        strategy="test",
        entry_price=100.0,
        target_price=110.0,
        stop_price=95.0,
        triggered_at=datetime.now(timezone.utc),
        matched_criteria={},
    )


def make_decision(action: str = "buy", symbol: str = "AAPL", notional: float = 100.0, confidence: float = 0.9) -> TradingDecision:
    return TradingDecision(
        action=action,
        symbol=symbol,
        notional_usd=notional,
        confidence=confidence,
        rationale="test rationale",
        risk_notes=[],
    )


def test_buy_success_caps_notional():
    payload = make_payload(symbol="NVDA", direction="bullish")
    decision = make_decision(action="buy", symbol="NVDA", notional=1000.0, confidence=0.95)
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    gated = apply_risk_gates(payload, decision, settings)

    assert gated.action == "buy"
    assert gated.symbol == "NVDA"
    # notional should be capped to max_notional_usd
    assert gated.notional_usd == 250.0


def test_buy_invalid_levels_skip():
    payload = make_payload(symbol="AAPL", direction="bullish")
    # make target <= entry to trigger the bullish-levels gate
    payload.entry_price = 100.0
    payload.target_price = 90.0
    payload.stop_price = 95.0

    decision = make_decision(action="buy", symbol="AAPL", notional=100.0, confidence=0.95)
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    gated = apply_risk_gates(payload, decision, settings)

    assert gated.action == "skip"
    assert gated.notional_usd == 0.0
    assert any("Bullish target" in note or "Bullish target" in gated.rationale for note in gated.risk_notes) or "Bullish target" in gated.rationale


def test_symbol_mismatch_skip():
    payload = make_payload(symbol="AAPL", direction="bullish")
    decision = make_decision(action="buy", symbol="MSFT", notional=100.0, confidence=0.95)
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    gated = apply_risk_gates(payload, decision, settings)

    assert gated.action == "skip"
    assert "symbol does not match" in gated.rationale or any("symbol does not match" in note for note in gated.risk_notes)


def test_sell_short_disabled_skip():
    payload = make_payload(symbol="TSLA", direction="bearish")
    decision = make_decision(action="sell_short", symbol="TSLA", notional=100.0, confidence=0.95)
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    gated = apply_risk_gates(payload, decision, settings)

    assert gated.action == "skip"
    assert any("Short selling is disabled" in note for note in gated.risk_notes) or "Short selling is disabled" in gated.rationale


def test_build_trade_decision_bullish_executes_buy():
    payload = make_payload(symbol="AAPL", direction="bullish")
    settings = SimpleNamespace(max_notional_usd=200.0, allow_short_selling=False)

    decision = build_trade_decision(payload, settings)

    assert decision.action == "buy"
    assert decision.notional_usd == 200.0
    assert decision.confidence == 1.0
    assert "bullish" in decision.rationale.lower()


def test_build_trade_decision_bearish_with_short_enabled():
    payload = make_payload(symbol="TSLA", direction="bearish")
    payload.entry_price = 100.0
    payload.target_price = 90.0
    payload.stop_price = 105.0
    settings = SimpleNamespace(max_notional_usd=150.0, allow_short_selling=True)

    decision = build_trade_decision(payload, settings)

    assert decision.action == "sell_short"
    assert decision.notional_usd == 150.0
    assert decision.confidence == 1.0


def test_build_trade_decision_neutral_skips():
    payload = make_payload(symbol="MSFT", direction="neutral")
    settings = SimpleNamespace(max_notional_usd=100.0, allow_short_selling=True)

    decision = build_trade_decision(payload, settings)

    assert decision.action == "skip"
    assert decision.notional_usd == 0.0
    assert "neutral" in decision.rationale.lower()


def test_optionomics_crush_pipeline_selects_iron_condor_strategy():
    payload = {
        "id": "idea-1",
        "symbol": "NVDA",
        "direction": "bullish",
        "strategy": "buy_call",
        "pipeline_short_name": "Crush",
        "pipeline_name": "Crush setups",
        "levels": {"entry": 128.4, "target": 138.0, "stop": 123.5, "current": 130.1, "peak": 131.2},
        "generated_at": "2026-08-05T13:35:00Z",
    }
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    from app.main import build_trade_decision_from_optionomics_payload

    decision = build_trade_decision_from_optionomics_payload(payload, settings)

    assert decision.action == "buy"
    assert decision.strategy == "iron_condor"
    assert "Crush" in decision.rationale


def test_optionomics_symbol_mismatch_is_rejected_before_execution():
    idea = {
        "id": "idea-symbol-check",
        "symbol": "MSFT",
        "direction": "bullish",
        "strategy": "bull_call_spread",
        "pipeline_short_name": "Swing",
        "pipeline_name": "Swing setups",
        "levels": {"entry": 498.0, "target": 520.0, "stop": 490.0, "current": 501.0, "peak": 503.0},
    }
    decision = TradingDecision(
        action="buy",
        symbol="NVDA",
        strategy="placeholder",
        notional_usd=250.0,
        confidence=0.9,
        rationale="test",
    )

    assert validate_optionomics_symbol_match(idea, decision) is False


def test_optionomics_neutral_crush_payload_is_valid_iron_condor():
    payload = {
        "id": "idea-neutral-crush",
        "symbol": "ULTA",
        "direction": "neutral",
        "strategy": "iron_condor",
        "pipeline_short_name": "Crush",
        "pipeline_name": "IV Crush",
        "levels": {"entry": 543.19, "target": 459.44, "stop": 626.94, "current": 530.63, "peak": 543.19},
        "generated_at": "2026-08-27T11:00:00.687140Z",
        "confidence_score": 0.88,
    }
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    from app.main import build_trade_decision_from_optionomics_payload

    decision = build_trade_decision_from_optionomics_payload(payload, settings)

    assert decision.action == "buy"
    assert decision.strategy == "iron_condor"
    assert decision.notional_usd == 250.0
    assert "Crush" in decision.rationale


def test_optionomics_bearish_direction_checks_its_range():
    payload = {
        "id": "idea-bearish-1",
        "symbol": "META",
        "direction": "bearish",
        "strategy": "short_put",
        "pipeline_short_name": "Momentum",
        "pipeline_name": "Momentum setups",
        "levels": {"entry": 500.0, "target": 470.0, "stop": 530.0, "current": 495.0, "peak": 510.0},
        "generated_at": "2026-08-05T13:35:00Z",
        "confidence_score": 0.8,
    }
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=True)

    from app.main import build_trade_decision_from_optionomics_payload

    decision = build_trade_decision_from_optionomics_payload(payload, settings)

    assert decision.action == "sell_short"
    assert decision.strategy == "placeholder"
    assert decision.notional_usd == 250.0


def test_select_valid_webull_option_contract_falls_back_to_nearest_expiration():
    chain = [
        {"symbol": "AAPL", "option_type": "CALL", "expiration_date": "2026-09-18", "strike_price": 200.0},
        {"symbol": "AAPL", "option_type": "CALL", "expiration_date": "2026-09-18", "strike_price": 205.0},
        {"symbol": "AAPL", "option_type": "CALL", "expiration_date": "2026-09-25", "strike_price": 210.0},
    ]

    selected = select_valid_webull_option_contract(
        chain,
        symbol="AAPL",
        option_type="CALL",
        expiration="2026-09-12",
        target_strike=203.0,
    )

    assert selected is not None
    assert selected["expiration_date"] == "2026-09-18"
    assert selected["strike_price"] == 205.0


def test_select_valid_webull_option_contract_uses_underlying_symbol_from_real_payload():
    chain = [
        {
            "symbol": "MRNA261218C00140000",
            "underlying_symbol": "MRNA",
            "option_type": "CALL",
            "expiration_date": "2026-09-18",
            "strike_price": 140.0,
        },
        {
            "symbol": "MRNA261218C00145000",
            "underlying_symbol": "MRNA",
            "option_type": "CALL",
            "expiration_date": "2026-09-18",
            "strike_price": 145.0,
        },
    ]

    selected = select_valid_webull_option_contract(
        chain,
        symbol="MRNA",
        option_type="CALL",
        expiration="2026-09-03",
        target_strike=140.5,
    )

    assert selected is not None
    assert selected["underlying_symbol"] == "MRNA"
    assert selected["strike_price"] == 140.0


def test_select_nearest_valid_webull_contract_uses_option_chain():
    from app.main import select_valid_webull_option_contract

    chain = {
        "data": [
            {"symbol": "CAI", "option_type": "CALL", "expiration_date": "2026-09-02", "strike_price": "25.00"},
            {"symbol": "CAI", "option_type": "CALL", "expiration_date": "2026-09-02", "strike_price": "30.00"},
            {"symbol": "CAI", "option_type": "PUT", "expiration_date": "2026-09-02", "strike_price": "25.00"},
        ]
    }

    selected = select_valid_webull_option_contract(
        chain,
        symbol="CAI",
        option_type="CALL",
        expiration="2026-09-02",
        target_strike=27.0,
    )

    assert selected is not None
    assert selected["strike_price"] == "25.00"

    list_selected = select_valid_webull_option_contract(
        chain["data"],
        symbol="CAI",
        option_type="CALL",
        expiration="2026-09-02",
        target_strike=27.0,
    )
    assert list_selected is not None
    assert list_selected["strike_price"] == "25.00"


def test_select_nearest_valid_webull_contract_falls_back_to_next_expiration():
    from app.main import select_valid_webull_option_contract

    chain = {
        "data": [
            {"symbol": "CRM", "option_type": "CALL", "expiration_date": "2026-09-18", "strike_price": "245.00"},
            {"symbol": "CRM", "option_type": "CALL", "expiration_date": "2026-09-18", "strike_price": "250.00"},
            {"symbol": "CRM", "option_type": "CALL", "expiration_date": "2026-09-18", "strike_price": "255.00"},
        ]
    }

    selected = select_valid_webull_option_contract(
        chain,
        symbol="CRM",
        option_type="CALL",
        expiration="2026-09-02",
        target_strike=249.0,
    )

    assert selected is not None
    assert selected["expiration_date"] == "2026-09-18"
    assert selected["strike_price"] == "250.00"


def test_optionomics_placeholder_pipeline_is_not_executed():
    payload = {
        "id": "idea-2",
        "symbol": "AAPL",
        "direction": "bullish",
        "strategy": "buy_call",
        "pipeline_short_name": "Swing",
        "pipeline_name": "Swing setups",
        "levels": {"entry": 100.0, "target": 110.0, "stop": 95.0, "current": 102.0, "peak": 103.5},
        "generated_at": "2026-08-05T13:35:00Z",
    }
    settings = SimpleNamespace(max_notional_usd=250.0, allow_short_selling=False)

    from app.main import build_trade_decision_from_optionomics_payload

    decision = build_trade_decision_from_optionomics_payload(payload, settings)

    assert decision.action == "buy"
    assert decision.strategy == "placeholder"
    assert "placeholder" in decision.rationale.lower()


def test_trade_idea_duplicate_is_skipped_by_ledger(tmp_path):
    from app.main import Ledger

    ledger = Ledger(str(tmp_path / "ledger.sqlite3"))
    idea = {
        "id": "idea-dup-1",
        "symbol": "NVDA",
        "direction": "bullish",
        "strategy": "buy_call",
        "pipeline_short_name": "Crush",
        "pipeline_name": "Crush setups",
        "levels": {"entry": 128.4, "target": 138.0, "stop": 123.5, "current": 130.1, "peak": 131.2},
    }

    assert ledger.is_trade_idea_seen("idea-dup-1") is False
    ledger.save_trade_idea("idea-dup-1", idea, status="queued")
    assert ledger.is_trade_idea_seen("idea-dup-1") is True


def test_build_option_order_request_prefers_contract_symbol(monkeypatch):
    from app.main import build_option_trade_request, OrderSide, OrderType

    monkeypatch.setattr("app.main.resolve_option_contract_symbol", lambda *args, **kwargs: "MSFT240919C00450000")

    decision = TradingDecision(
        action="buy",
        symbol="MSFT",
        strategy="iron_condor",
        notional_usd=250.0,
        confidence=0.9,
        rationale="test",
        risk_notes=[],
    )

    request = build_option_trade_request(decision, direction="bullish", notional_usd=250.0)

    assert request.symbol == "MSFT240919C00450000"
    assert request.side == OrderSide.BUY
    assert request.type == OrderType.MARKET
    assert request.notional == 250.0

    import os
    if os.path.exists("test_duplicate_ledger.sqlite3"):
        os.remove("test_duplicate_ledger.sqlite3")
