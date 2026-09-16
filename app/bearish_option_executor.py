from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


class BearishPutOptionExecutor:
    """Executes bearish directional option ideas with Webull PUT bracket orders."""

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
        return "PUT"

    @staticmethod
    def strategy_label() -> str:
        return "sell_next_way"

    @staticmethod
    def order_builder_name() -> str:
        return "buy_put_with_bracket"

    def submit(
        self,
        *,
        account_id: str,
        symbol: str,
        strike: float,
        expiration: str,
        quantity: int,
        entry_limit: float | None = None,
        profit_percent: float = 20,
        stop_loss_percent: float = 10,
    ) -> dict[str, Any]:
        builder = getattr(self.module, self.order_builder_name())
        return builder(
            account_id=account_id,
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            quantity=quantity,
            entry_limit=entry_limit,
            profit_percent=profit_percent,
            stop_loss_percent=stop_loss_percent,
        )
