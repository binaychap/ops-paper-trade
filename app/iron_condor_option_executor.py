from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import importlib.util
from pathlib import Path
from typing import Any


def round_to_tick(price: float, tick_size: float) -> float:
    price_decimal = Decimal(str(price))
    tick_decimal = Decimal(str(tick_size))
    ticks = (price_decimal / tick_decimal).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(ticks * tick_decimal)


class IronCondorOptionExecutor:
    """Executor for placing iron-condor style combo orders via the Webull combo API."""

    def __init__(self, module: Any | None = None):
        self.module = module or self._load_option_module()

    @staticmethod
    def _load_option_module() -> Any:
        module_path = Path(__file__).resolve().parent / "webull-buy-combo-option.py"
        spec = importlib.util.spec_from_file_location("webull_combo_option_executor", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load Webull option module from {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def option_type() -> str:
        return "IRON_CONDOR"

    @staticmethod
    def strategy_label() -> str:
        return "iron_condor"

    def submit(
        self,
        *,
        account_id: str,
        symbol: str,
        expiration: str,
        reference_price: float,
        quantity: int = 1,
        wing_width: float | None = None,
        trade_client=None,
        *,
        profit_percent: float = 10,
        stop_loss_percent: float = 5,
        exit_time_in_force: str = "DAY",
    ) -> dict[str, Any]:
        """
        Build a simple iron-condor using four legs:
        - Sell call at +width, Buy call at +2*width
        - Sell put at -width, Buy put at -2*width

        The implementation uses the helper `option_leg` from the loaded module and
        then submits a MASTER combo order (net debit/credit semantics depend on the legs).
        """
        trade_client = trade_client or getattr(self.module, "get_trade_client")()
        symbol_u = str(symbol).upper()
        tick = 0.05
        ref = float(reference_price)
        # Default wing width is 5% of reference rounded to nearest 5-cent tick
        computed_width = float(wing_width or max(1.0, round(ref * 0.05)))
        # normalize to a round strike multiple of 5
        sell_call_strike = round((ref + computed_width) / 5.0) * 5.0
        buy_call_strike = round((ref + computed_width * 2) / 5.0) * 5.0
        sell_put_strike = round((ref - computed_width) / 5.0) * 5.0
        buy_put_strike = round((ref - computed_width * 2) / 5.0) * 5.0

        legs = [
            self.module.option_leg(symbol_u, sell_call_strike, expiration, "CALL", "SELL", quantity),
            self.module.option_leg(symbol_u, buy_call_strike, expiration, "CALL", "BUY", quantity),
            self.module.option_leg(symbol_u, sell_put_strike, expiration, "PUT", "SELL", quantity),
            self.module.option_leg(symbol_u, buy_put_strike, expiration, "PUT", "BUY", quantity),
        ]

        master = {
            "client_order_id": self.module.new_id(),
            "combo_type": "MASTER",
            "option_strategy": "IRON_CONDOR",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol_u,
            "order_type": "MARKET",
            "quantity": str(quantity),
            "side": "BUY",
            "time_in_force": "DAY",
            "entrust_type": "QTY",
            "position_intent": "BUY_TO_OPEN",
            "legs": legs,
        }

        # compute take profit and stop prices based on reference and supplied percentages
        tick_size = 0.05
        entry_estimate = ref
        take_profit_price = round_to_tick(entry_estimate * (1 + profit_percent / 100), tick_size)
        stop_price = round_to_tick(entry_estimate * (1 - stop_loss_percent / 100), tick_size)

        take_profit_order = {
            "client_order_id": self.module.new_id(),
            "combo_type": "STOP_PROFIT",
            "option_strategy": "IRON_CONDOR",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol_u,
            "order_type": "LIMIT",
            "limit_price": f"{take_profit_price:.2f}",
            "quantity": str(quantity),
            "side": "SELL",
            "time_in_force": exit_time_in_force,
            "entrust_type": "QTY",
            "legs": [
                self.module.option_leg(symbol_u, sell_call_strike, expiration, "CALL", "SELL", quantity),
                self.module.option_leg(symbol_u, sell_put_strike, expiration, "PUT", "SELL", quantity),
            ],
        }

        stop_loss_order = {
            "client_order_id": self.module.new_id(),
            "combo_type": "STOP_LOSS",
            "option_strategy": "IRON_CONDOR",
            "instrument_type": "OPTION",
            "market": "US",
            "symbol": symbol_u,
            "order_type": "STOP_LOSS",
            "stop_price": f"{stop_price:.2f}",
            "quantity": str(quantity),
            "side": "SELL",
            "time_in_force": exit_time_in_force,
            "entrust_type": "QTY",
            "legs": [
                self.module.option_leg(symbol_u, sell_call_strike, expiration, "CALL", "SELL", quantity),
                self.module.option_leg(symbol_u, sell_put_strike, expiration, "PUT", "SELL", quantity),
            ],
        }

        new_orders = [master, take_profit_order, stop_loss_order]
        combo_id = self.module.new_id()
        response = trade_client.order_v3.place_order(account_id, new_orders, client_combo_order_id=combo_id)

        if response.status_code == 200:
            result = response.json()
            return {**result, "combo_id": combo_id, "entry_id": master["client_order_id"], "profit_id": take_profit_order["client_order_id"], "stop_id": stop_loss_order["client_order_id"]}

        raise RuntimeError(f"Iron condor order failed: {response.status_code} {response.text}")
