"""Option bracket combo orders."""

import json

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
        "market": "US"
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
                quantity=quantity
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
        "time_in_force": "DAY",

        "entrust_type": "QTY",

        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="CALL",
                side="SELL",
                quantity=quantity
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

        "time_in_force": "DAY",

        "entrust_type": "QTY",

        "legs": [
            option_leg(
                symbol=symbol,
                strike=strike,
                expiration=expiration,
                option_type="CALL",
                side="SELL",
                quantity=quantity
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
    #
    # Depending on SDK release, client_combo_order_id may be
    # accepted by the combo-order overload/body.
    # --------------------------------------------------------

    response = trade_client.order_v3.place_order(
        account_id,
        new_orders,
        client_combo_order_id=combo_id
    )

    if response.status_code == 200:
        result = response.json()

        print("\nOrder submitted successfully:")
        print(json.dumps(result, indent=2))

        return result

    raise RuntimeError(
        f"Order failed: "
        f"{response.status_code} {response.text}"
    )


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