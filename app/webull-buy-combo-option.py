"""Option bracket combo orders."""

import json
import os
import sys
import logging
from webull.core.client import ApiClient
from webull.data.data_client import DataClient

from app.webull_broker import get_account_id, get_trade_client, new_id


# ============================================================
# COMMON OPTION LEG
# ============================================================

def option_leg(
    symbol: str,
    strike: float,
    expiration: str,
    option_type: str,
    side: str,
    quantity: int
):
    return {
        "side": side,
        "quantity": str(quantity),
        "symbol": symbol.upper(),
        "strike_price": f"{strike:.2f}",
        "option_expire_date": expiration,
        "instrument_type": "OPTION",
        "option_type": option_type.upper(),
        "market": "US",
    }

from decimal import Decimal, ROUND_HALF_UP


def round_to_tick(price: float, tick_size: float) -> float:
    price_decimal = Decimal(str(price))
    tick_decimal = Decimal(str(tick_size))

    ticks = (
        price_decimal / tick_decimal
    ).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP
    )

    return float(ticks * tick_decimal)


def _find_valid_contract(symbol: str, desired_expiration: str | None, desired_strike: float | None) -> tuple[str, float, str | None]:
    """Query Webull option contracts and return a validated (expiration, strike).

    If the exact expiration/strike aren't available, pick the closest matching values.
    Raises RuntimeError if no suitable contract found.
    """
    symbol = str(symbol or "").upper()
    app_key = os.getenv("WEBULL_APP_KEY")
    app_secret = os.getenv("WEBULL_APP_SECRET")
    endpoint = os.getenv("WEBULL_ENDPOINT") or "api.sandbox.webull.com"
    if not app_key or not app_secret:
        raise RuntimeError("WEBULL credentials not configured for contract validation")

    api_client = ApiClient(app_key, app_secret, "us")
    api_client.add_endpoint("us", endpoint)
    api_client.set_stream_logger(stream=sys.stdout)

    data_client = DataClient(api_client)

    # Try to fetch contracts for the requested expiration first
    try:
        resp = data_client.instrument.get_option_contracts(
            category="US_OPTION",
            underlying_symbols=symbol,
            start_date=desired_expiration,
            end_date=desired_expiration,
            page_size=500,
        )
        if resp is None or (hasattr(resp, "status_code") and resp.status_code != 200):
            # fallback: fetch all expirations
            resp = data_client.instrument.get_option_contracts(
                category="US_OPTION",
                underlying_symbols=symbol,
                page_size=500,
            )
        payload = resp.json() if resp is not None and hasattr(resp, "json") else resp
    except Exception as exc:
        raise RuntimeError(f"Unable to query option chain for {symbol}: {exc}")

    items = []
    if isinstance(payload, dict):
        # payload may contain 'data' or similar
        for key in ("data", "items", "contracts", "options", "result", "results"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                items = maybe
                break
    elif isinstance(payload, list):
        items = payload

    expirations: dict[str, dict[float, dict]] = {}
    for item in items:
        exp = item.get("expiration_date") or item.get("expiration") or item.get("exp_date") or item.get("expire_date") or item.get("expiry")
        if not exp:
            continue
        strike_raw = item.get("strike_price") or item.get("strike") or item.get("strikePrice")
        try:
            strike = float(strike_raw)
        except Exception:
            continue
        # collect the full item for this expiration+strike
        expirations.setdefault(exp, {})[strike] = item

    if not expirations:
        raise RuntimeError(f"No option contracts found for {symbol}")

    # If desired expiration exists, prefer it; otherwise pick the nearest available expiration
    chosen_exp = None
    if desired_expiration and desired_expiration in expirations:
        chosen_exp = desired_expiration
    else:
        # pick any expiration (prefer the earliest)
        chosen_exp = sorted(expirations.keys())[0]

    available_strikes = sorted(expirations[chosen_exp].keys())
    if not available_strikes:
        raise RuntimeError(f"No strikes found for {symbol} expiration {chosen_exp}")

    if desired_strike is None:
        # choose nearest-to-the-money (median) as a fallback
        strike = available_strikes[len(available_strikes) // 2]
    else:
        # pick the closest available strike
        strike = min(available_strikes, key=lambda s: abs(s - float(desired_strike)))

    item = expirations[chosen_exp][strike]
    # try to read contract symbol fields commonly returned by Webull
    contract_symbol = (
        item.get("symbol")
        or item.get("ticker")
        or item.get("option_symbol")
        or item.get("contract_symbol")
        or None
    )

    logger = logging.getLogger(__name__)
    logger.info(
        "Validated contract for %s: chosen_expiration=%s chosen_strike=%.2f contract_symbol=%s (desired_expiration=%s desired_strike=%s)",
        symbol,
        chosen_exp,
        float(strike),
        contract_symbol,
        desired_expiration,
        desired_strike,
    )
    # For debugging, include available expirations count at DEBUG level
    logger.debug("Available expirations for %s: %s", symbol, {k: len(v) for k, v in expirations.items()})
    return chosen_exp, float(strike), contract_symbol


# ============================================================
# BUY OPTION + TAKE PROFIT + STOP LOSS
# ============================================================

def buy_call_with_bracket(
    account_id: str,
    symbol: str,
    strike: float,
    expiration: str,
    quantity: int,
    entry_limit: float,
    profit_percent: float = 10,
    stop_loss_percent: float = 5,
    trade_client=None,
    *,
    exit_time_in_force: str = "DAY",
):
    trade_client = trade_client or get_trade_client()
    """
    Example:

    Entry:
        BUY AAPL 220 CALL @ $11.25

    Take Profit:
        SELL @ +10% = $12.38

    Stop Loss:
        SELL when premium reaches -5% = $10.69
    """

    symbol = symbol.upper()
    tick_size = 0.05
    # Validate/adjust expiration and strike against broker data
    try:
        expiration, strike, contract_symbol = _find_valid_contract(symbol, expiration, strike)
    except Exception as exc:
        raise RuntimeError(f"Contract validation failed: {exc}")
    entry_limit = round_to_tick(
        entry_limit,
        tick_size
    )
    take_profit_price = round_to_tick(
        entry_limit * (1 + profit_percent / 100),
        tick_size
    )

    stop_price = round_to_tick(
        entry_limit * (1 - stop_loss_percent / 100),
        tick_size
    )

    combo_id = new_id()

    # --------------------------------------------------------
    # 1. MASTER ENTRY ORDER
    # --------------------------------------------------------

    master_order = {
        "client_order_id": new_id(),

        "combo_type": "MASTER",

        "option_strategy": "SINGLE",

        "instrument_type": "OPTION",
        "market": "US",

        "symbol": symbol,

        "order_type": "LIMIT",

        "limit_price": f"{entry_limit:.2f}",

        "quantity": str(quantity),

        "side": "BUY",

        "time_in_force": "DAY",

        "entrust_type": "QTY",

        # For combo options, position intent belongs on MASTER
        "position_intent": "BUY_TO_OPEN",

        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="CALL",
                side="BUY",
                quantity=quantity,
            )
        ]
    }

    # --------------------------------------------------------
    # 2. TAKE PROFIT
    #
    # +10%
    #
    # $11.25 * 1.10 = $12.375 -> $12.38
    # --------------------------------------------------------

    take_profit_order = {
        "client_order_id": new_id(),

        "combo_type": "STOP_PROFIT",

        "option_strategy": "SINGLE",

        "instrument_type": "OPTION",
        "market": "US",

        "symbol": symbol,

        "order_type": "LIMIT",

        "limit_price": f"{take_profit_price:.2f}",

        "quantity": str(quantity),

        "side": "SELL",

        # Webull option sell orders require DAY
        "time_in_force": exit_time_in_force,

        "entrust_type": "QTY",

        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="CALL",
                side="SELL",
                quantity=quantity,
            )
        ]
    }

    # --------------------------------------------------------
    # 3. STOP LOSS
    #
    # -5%
    #
    # $11.25 * 0.95 = $10.6875 -> $10.69
    # --------------------------------------------------------

    stop_loss_order = {
        "client_order_id": new_id(),

        "combo_type": "STOP_LOSS",

        "option_strategy": "SINGLE",

        "instrument_type": "OPTION",
        "market": "US",

        "symbol": symbol,

        "order_type": "STOP_LOSS",

        "stop_price": f"{stop_price:.2f}",

        "quantity": str(quantity),

        "side": "SELL",

        "time_in_force": exit_time_in_force,

        "entrust_type": "QTY",

        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="CALL",
                side="SELL",
                quantity=quantity,
            )
        ]
    }

    new_orders = [
        master_order,
        take_profit_order,
        stop_loss_order
    ]

    print("=" * 60)
    print("OPTION BRACKET ORDER")
    print("=" * 60)

    print(f"Contract:      {symbol} {strike} CALL")
    print(f"Expiration:    {expiration}")
    print(f"Quantity:      {quantity}")
    print(f"Entry Limit:   ${entry_limit:.2f}")
    print(
        f"Take Profit:   ${take_profit_price:.2f} "
        f"(+{profit_percent}%)"
    )
    print(
        f"Stop Loss:     ${stop_price:.2f} "
        f"(-{stop_loss_percent}%)"
    )
    print(f"Combo ID:      {combo_id}")

    print("\nOrders:")
    print(json.dumps(new_orders, indent=2))
    # --------------------------------------------------------
    # SUBMIT
    # Depending on SDK release, client_combo_order_id may be
    # accepted by the combo-order overload/body.
    # --------------------------------------------------------
    logger = logging.getLogger(__name__)
    logger.info("Submitting option combo for %s %s %s", symbol, strike, expiration)
    logger.debug("Order payload: %s", json.dumps(new_orders))
    try:
        response = trade_client.order_v3.place_order(
            account_id,
            new_orders,
            client_combo_order_id=combo_id,
        )
    except Exception as exc:
        logger.exception("Broker submission raised an exception for contract %s %s %s", symbol, strike, expiration)
        raise

    if response.status_code == 200:
        result = response.json()
        logger.info("Order submitted successfully: %s", result)
        return result

    raise RuntimeError(f"Order failed: {response.status_code} {response.text}")


def buy_put_with_bracket(
    account_id: str,
    symbol: str,
    strike: float,
    expiration: str,
    quantity: int,
    entry_limit: float,
    profit_percent: float = 10,
    stop_loss_percent: float = 5,
    trade_client=None,
    *,
    exit_time_in_force: str = "DAY",
):
    trade_client = trade_client or get_trade_client()
    symbol = symbol.upper()
    # Validate/adjust expiration and strike against broker data
    try:
        expiration, strike, contract_symbol = _find_valid_contract(symbol, expiration, strike)
    except Exception as exc:
        raise RuntimeError(f"Contract validation failed: {exc}")
    tick_size = 0.05
    entry_limit = round_to_tick(entry_limit, tick_size)
    take_profit_price = round_to_tick(entry_limit * (1 + profit_percent / 100), tick_size)
    stop_price = round_to_tick(entry_limit * (1 - stop_loss_percent / 100), tick_size)
    combo_id = new_id()

    master_order = {
        "client_order_id": new_id(),
        "combo_type": "MASTER",
        "option_strategy": "SINGLE",
        "instrument_type": "OPTION",
        "market": "US",
        "symbol": symbol,
        "order_type": "LIMIT",
        "limit_price": f"{entry_limit:.2f}",
        "quantity": str(quantity),
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "position_intent": "BUY_TO_OPEN",
        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="PUT",
                side="BUY",
                quantity=quantity,
            )
        ],
    }

    take_profit_order = {
        "client_order_id": new_id(),
        "combo_type": "STOP_PROFIT",
        "option_strategy": "SINGLE",
        "instrument_type": "OPTION",
        "market": "US",
        "symbol": symbol,
        "order_type": "LIMIT",
        "limit_price": f"{take_profit_price:.2f}",
        "quantity": str(quantity),
        "side": "SELL",
        "time_in_force": exit_time_in_force,
        "entrust_type": "QTY",
        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="PUT",
                side="SELL",
                quantity=quantity,
            )
        ],
    }

    stop_loss_order = {
        "client_order_id": new_id(),
        "combo_type": "STOP_LOSS",
        "option_strategy": "SINGLE",
        "instrument_type": "OPTION",
        "market": "US",
        "symbol": symbol,
        "order_type": "STOP_LOSS",
        "stop_price": f"{stop_price:.2f}",
        "quantity": str(quantity),
        "side": "SELL",
        "time_in_force": exit_time_in_force,
        "entrust_type": "QTY",
        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="PUT",
                side="SELL",
                quantity=quantity,
            )
        ],
    }

    new_orders = [master_order, take_profit_order, stop_loss_order]
    logger = logging.getLogger(__name__)
    logger.info("Submitting option combo for %s %s %s", symbol, strike, expiration)
    logger.debug("Order payload: %s", json.dumps(new_orders))
    try:
        response = trade_client.order_v3.place_order(account_id, new_orders, client_combo_order_id=combo_id)
    except Exception:
        logger.exception("Broker submission raised an exception for contract %s %s %s", symbol, strike, expiration)
        raise

    if response.status_code == 200:
        return response.json()

    raise RuntimeError(f"Order failed: {response.status_code} {response.text}")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    account_id = get_account_id()
    print("Using account:", account_id)

    buy_call_with_bracket(
        account_id=account_id,

        symbol="AAPL",

        strike=220,

        expiration="2026-09-18",

        quantity=1,

        # Buy call at max $11.25
        entry_limit=11.25,

        # Sell for profit at +10%
        profit_percent=10,

        # Stop out at -5%
        stop_loss_percent=5,
    )

    # Example bearish put bracket:
    # buy_put_with_bracket(
    #     account_id=account_id,
    #     symbol="AAPL",
    #     strike=230,
    #     expiration="2026-09-18",
    #     quantity=1,
    #     entry_limit=11.25,
    #     profit_percent=10,
    #     stop_loss_percent=5,
    # )