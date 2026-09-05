"""Stock bracket combo orders."""

import json

from app.webull_broker import get_account_id, get_trade_client, new_id


def buy_stock(
    account_id: str,
    symbol: str,
    quantity: int,
    entry_price: float,
    stop_price: float,
    target_price: float,
    trade_client=None,
):
    trade_client = trade_client or get_trade_client()
    symbol = symbol.upper()
    combo_id = new_id()

    master_order = {
        "client_order_id": new_id(),
        "combo_type": "MASTER",
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "side": "BUY",
        "order_type": "LIMIT",
        "limit_price": f"{float(entry_price):.2f}",
        "quantity": str(quantity),
        "time_in_force": "DAY",
        "support_trading_session": "CORE",
        "entrust_type": "QTY",
    }

    take_profit_order = {
        "client_order_id": new_id(),
        "combo_type": "STOP_PROFIT",
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "side": "SELL",
        "order_type": "LIMIT",
        "limit_price": f"{float(target_price):.2f}",
        "quantity": str(quantity),
        "time_in_force": "DAY",
        "support_trading_session": "CORE",
        "entrust_type": "QTY",
    }

    stop_loss_order = {
        "client_order_id": new_id(),
        "combo_type": "STOP_LOSS",
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "market": "US",
        "side": "SELL",
        "order_type": "STOP_LOSS",
        "stop_price": f"{float(stop_price):.2f}",
        "quantity": str(quantity),
        "time_in_force": "DAY",
        "support_trading_session": "CORE",
        "entrust_type": "QTY",
    }

    new_orders = [master_order, take_profit_order, stop_loss_order]
    response = trade_client.order_v3.place_order(account_id, new_orders, client_combo_order_id=combo_id)
    if response.status_code != 200:
        raise RuntimeError(f"Stock order failed: {response.status_code} {response.text}")

    result = response.json()
    print("\nStock bracket combo submitted successfully:")
    print(json.dumps({"client_combo_order_id": combo_id, "new_orders": new_orders}, indent=2))
    print(json.dumps(result, indent=2))
    return result


