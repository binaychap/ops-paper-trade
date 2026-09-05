from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_webull_combo_module() -> Any:
    # allow tests to monkeypatch loader by setting app.main._load_webull_combo_module
    try:
        import importlib
        main_mod = importlib.import_module("app.main")
        loader = getattr(main_mod, "_load_webull_combo_module", None)
        if callable(loader) and loader is not _load_webull_combo_module:
            return loader()
    except Exception:
        # ignore and fall back to default loader
        pass

    module_path = Path(__file__).resolve().parent / "webull-buy-combo-option.py"
    spec = importlib.util.spec_from_file_location("webull_combo_option", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Webull combo module from {module_path}")

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

    webull_module = _load_webull_combo_module()
    account_id = webull_module.get_account_id()

    reference_level = None
    if payload is not None:
        reference_level = payload.entry_price or payload.target_price or payload.stop_price
    if reference_level is None:
        reference_level = max(float(d.get("notional_usd") or 0.0) / 100.0, 1.0)

    quantity = 1
    entry_price = float(payload.entry_price) if payload is not None and getattr(payload, "entry_price", None) is not None else max(float(d.get("notional_usd") or 0.0) / 100.0, 0.01)
    stop_price = float(payload.stop_price) if payload is not None and getattr(payload, "stop_price", None) is not None else entry_price * 0.95
    target_price = float(payload.target_price) if payload is not None and getattr(payload, "target_price", None) is not None else entry_price * 1.10

    order_result = webull_module.buy_stock(
        account_id=account_id,
        symbol=d.get("symbol"),
        quantity=quantity,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )

    return {
        "dry_run": False,
        "id": str(order_result.get("order_id") or order_result.get("client_order_id") or ""),
        "client_order_id": client_order_id,
        "symbol": d.get("symbol"),
        "status": "submitted",
        "side": "BUY" if d.get("action") == "buy" else "SELL",
        "notional_usd": d.get("notional_usd"),
        "broker": "webull",
    }


__all__ = ["submit_paper_order", "_is_webull_rate_limit_error"]
