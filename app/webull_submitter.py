from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _load_webull_stock_module() -> Any:
    module_path = Path(__file__).resolve().parent / "webull-buy-combo-stock.py"
    spec = importlib.util.spec_from_file_location("webull_combo_stock", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Webull stock combo module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_webull_option_module() -> Any:
    module_path = Path(__file__).resolve().parent / "webull-buy-combo-option.py"
    spec = importlib.util.spec_from_file_location("webull_combo_option", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Webull option module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_webull_rate_limit_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "429" in message or "too_many_requests" in message or "too many requests" in message or "rate limit" in message


def submit_paper_order(decision: Any, settings: Any, fingerprint: str, payload: Any | None = None) -> dict[str, Any]:
    # Accept either a dict decision or an object with model_dump / attributes
    if hasattr(decision, "model_dump"):
        d = decision.model_dump(mode="json")
    elif isinstance(decision, dict):
        d = decision
    else:
        # fallback: try attribute access
        d = {
            "action": getattr(decision, "action", "skip"),
            "symbol": getattr(decision, "symbol", ""),
            "notional_usd": getattr(decision, "notional_usd", 0.0),
        }

    client_order_id = f"om-{fingerprint[:24]}"
    if settings.dry_run:
        return {
            "dry_run": True,
            "client_order_id": client_order_id,
            "symbol": d.get("symbol"),
            "action": d.get("action"),
            "notional_usd": d.get("notional_usd"),
        }

    action = d.get("action")
    symbol = d.get("symbol")
    reference_level = None
    if payload is not None:
        reference_level = payload.entry_price or payload.target_price or payload.stop_price
    if reference_level is None:
        reference_level = max(float(d.get("notional_usd") or 0.0) / 100.0, 1.0)

    if action == "sell_short":
        from app.bearish_option_executor import BearishPutOptionExecutor
        option_module = _load_webull_option_module()
        executor = BearishPutOptionExecutor(module=option_module)
        expiry = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
        strike = round(float(reference_level) / 5.0) * 5.0
        entry_limit = max(float(strike) * 0.08, 1.0)
        try:
            order_result = executor.submit(
                account_id=option_module.get_account_id(),
                symbol=symbol,
                strike=strike,
                expiration=expiry,
                quantity=1,
                entry_limit=entry_limit,
                profit_percent=10,
                stop_loss_percent=5,
            )
        except Exception as exc:
            # If no option contracts are available, treat this idea as skipped.
            msg = str(exc)
            if "No option contracts found" in msg or "Contract validation failed" in msg:
                return {"skipped": True, "reason": msg}
            raise
        return {
            "dry_run": False,
            "id": str(order_result.get("order_id") or order_result.get("client_order_id") or ""),
            "client_order_id": client_order_id,
            "symbol": symbol,
            "status": "submitted",
            "side": "SELL",
            "notional_usd": d.get("notional_usd"),
            "broker": "webull",
            "option": {"type": executor.option_type(), "strategy": executor.strategy_label()},
        }

    # Handle neutral Optionomics iron-condor ideas by submitting an iron-condor combo
    if payload is not None and getattr(payload, "direction", None) == "neutral":
        from app.iron_condor_option_executor import IronCondorOptionExecutor

        option_module = _load_webull_option_module()
        executor = IronCondorOptionExecutor(module=option_module)
        expiry = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
        reference = float(getattr(payload, "entry_price", None) or getattr(payload, "target_price", None) or getattr(payload, "stop_price", None) or max(float(d.get("notional_usd") or 0.0) / 100.0, 1.0))

        try:
            order_result = executor.submit(
                account_id=option_module.get_account_id(),
                symbol=symbol,
                expiration=expiry,
                reference_price=reference,
                exit_time_in_force="GTC",
                quantity=1,
            )
        except Exception as exc:
            msg = str(exc)
            if "No option contracts found" in msg or "Contract validation failed" in msg:
                return {"skipped": True, "reason": msg}
            raise

        return {
            "dry_run": False,
            "id": str(order_result.get("order_id") or order_result.get("client_order_id") or ""),
            "client_order_id": client_order_id,
            "symbol": symbol,
            "status": "submitted",
            "side": "IRON_CONDOR",
            "notional_usd": d.get("notional_usd"),
            "broker": "webull",
            "option": {"type": executor.option_type(), "strategy": executor.strategy_label()},
        }

    webull_module = _load_webull_stock_module()
    account_id = webull_module.get_account_id()

    quantity = 1
    execute_at_market = bool(d.get("execute_at_market", False))
    # Determine a stable reference price for stop/target even when entry is a market order.
    reference = (
        float(payload.entry_price)
        if payload is not None and getattr(payload, "entry_price", None) is not None
        else max(float(d.get("notional_usd") or 0.0) / 100.0, 0.01)
    )
    entry_price = None if execute_at_market else reference
    stop_price = round(reference * 0.95, 2)
    target_price = round(reference * 1.10, 2)

    order_kwargs = dict(
        account_id=account_id,
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )

    if getattr(settings, "next_day_exit_enabled", False):
        if action != "buy":
            raise ValueError("Next-day stock exits support long buy entries only")
        from app.ledger import Ledger
        from app.stock_execution import StockExecution

        ledger = Ledger(settings.database_path)
        if ledger.has_active_exit_job(account_id=account_id, symbol=symbol):
            raise RuntimeError(f"Duplicate active stock exit job already exists for {symbol} on account {account_id}")

        with ledger.exit_worker_lock() as acquired:
            if not acquired:
                raise RuntimeError("Stock exit worker busy; entry not submitted")
            broker = StockExecution(webull_module.get_trade_client())
            if broker.position(account_id, symbol) != 0:
                raise ValueError("Scheduled entry requires no existing stock position for this symbol")

            tracked_job = None

            def record_intent(tracking):
                nonlocal tracked_job
                job = {
                    "id": client_order_id, "account_id": account_id,
                    "symbol": symbol, "quantity": str(quantity),
                    **tracking, "status": "waiting_entry", "market_orders": [],
                    "due_at": None, "last_error": None,
                }
                ledger.register_exit_job(job)
                tracked_job = job

            try:
                order_result = webull_module.buy_stock(
                    **order_kwargs, exit_time_in_force="GTC", before_submit=record_intent,
                )
            except Exception as exc:
                if tracked_job is not None:
                    tracked_job["entry_submission_error"] = str(exc)[:500]
                    tracked_job["last_error"] = "Entry submission was not confirmed. Broker reconciliation is required."
                    ledger.save_exit_job(tracked_job)
                raise
    else:
        order_result = webull_module.buy_stock(**order_kwargs)

    return {
        "dry_run": False,
        "id": str(order_result.get("order_id") or order_result.get("client_order_id") or ""),
        "client_order_id": client_order_id,
        "symbol": symbol,
        "status": "submitted",
        "side": "BUY" if action == "buy" else "SELL",
        "notional_usd": d.get("notional_usd"),
        "broker": "webull",
        "bracket": {key: order_result.get(key) for key in ("combo_id", "entry_id", "profit_id", "stop_id")},
    }


__all__ = ["submit_paper_order", "_is_webull_rate_limit_error"]
