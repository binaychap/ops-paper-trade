"""One-shot bullish-flow stock bracket runner; defaults to DRY_RUN=true."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import re
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.bullish_ledger import BullishLedger
from app.webull_quotes import QuoteError, current_stock_quote
from app.webull_errors import describe_webull_error
from app.webull_submitter import _load_webull_stock_module

logger = logging.getLogger(__name__)


class BullishSettings(BaseSettings):
    dry_run: bool = Field(default=True, alias='DRY_RUN')
    database_path: str = Field(default='bot.sqlite3', alias='DATABASE_PATH')
    max_notional_usd: float = Field(default=250, gt=0, allow_inf_nan=False, alias='MAX_NOTIONAL_USD')
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / '.env', extra='ignore',
    )


def load_feed():
    spec = importlib.util.spec_from_file_location(
        'app.top_bullish', Path(__file__).with_name('top-bullish.py'),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TopBullish()


class MainTopBullish:
    def __init__(self, *, settings=None, feed=None, stock_loader=None, quote_provider=None):
        self.settings = settings or BullishSettings()
        self.ledger = BullishLedger(self.settings.database_path)
        self.quote_provider = quote_provider or current_stock_quote
        self.feed = feed
        self.stock_loader = stock_loader or _load_webull_stock_module

    def run(self, *, limit=10):
        """Process feed symbols once using fresh Webull quotes as limit prices."""
        payload = (self.feed or load_feed()).fetch(limit=limit)
        if not isinstance(payload, list):
            raise ValueError('Expected a list of bullish flow entries')
        results = []
        for item in payload:
            try:
                results.append(self.process(item))
            except (ValueError, TypeError, KeyError) as exc:
                results.append({'status': 'skipped', 'reason': str(exc)})
        return results

    def process(self, item):
        if not isinstance(item, dict):
            raise ValueError('Bullish flow entry must be an object')
        symbol = str(item.get('symbol') or '').strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9.]{0,9}', symbol):
            raise ValueError('Invalid bullish symbol')
        # The aggregate feed has no trade ID; symbol is the durable identity.
        trade_id = str(item.get('trade_id') or item.get('id') or f'bullish:{symbol}')
        if self.ledger.contains(trade_id, symbol):
            return {'symbol': symbol, 'status': 'skipped', 'reason': 'Trade or symbol already present'}
        premium = float(item['total_premium'])
        count = item['trade_count']
        if not math.isfinite(premium) or premium < 0:
            raise ValueError(f'{symbol}: invalid total_premium')
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f'{symbol}: invalid trade_count')
        try:
            quote = self.quote_provider(symbol)
        except QuoteError as exc:
            logger.warning('Webull quote for %s: %s', symbol, exc)
            return {'symbol': symbol, 'status': 'skipped', 'reason': f'Quote unavailable: {exc}'}
        except Exception as exc:
            detail = describe_webull_error(exc)
            logger.error('Webull quote for %s failed: %s', symbol, detail)
            return {'symbol': symbol, 'status': 'skipped', 'reason': f'Quote unavailable: {detail}'}
        entry = round(float(quote['price']), 2)
        stop, target = round(entry * 0.95, 2), round(entry * 1.10, 2)
        if not math.isfinite(entry) or not 0 < stop < entry < target:
            raise ValueError(f'{symbol}: invalid bracket prices')
        if entry > self.settings.max_notional_usd:
            return {'symbol': symbol, 'status': 'skipped', 'reason': 'One share exceeds MAX_NOTIONAL_USD'}
        order_request = dict(symbol=symbol, quantity=1, entry_price=entry,
                             stop_price=stop, target_price=target)
        normalized = {**item, 'symbol': symbol, 'total_premium': premium, 'trade_count': count, 'entry_quote': quote}
        if not self.ledger.claim(trade_id, normalized, order_request):
            return {'symbol': symbol, 'status': 'skipped', 'reason': 'Trade or symbol already present'}
        if self.settings.dry_run:
            self.ledger.update(trade_id, 'dry_run')
            return {'symbol': symbol, 'status': 'dry_run', 'order': order_request}
        attempted = False

        def before_submit(tracking):
            nonlocal attempted
            self.ledger.update(trade_id, 'submitting', tracking=tracking)
            attempted = True

        try:
            stock = self.stock_loader()
            result = stock.buy_stock(
                account_id=stock.get_account_id(), **order_request,
                before_submit=before_submit,
            )
            self.ledger.update(trade_id, 'submitted', order=result)
            return {'symbol': symbol, 'status': 'submitted'}
        except Exception as exc:
            # A timeout after submission may still mean the broker accepted it.
            status = 'submission_unknown' if attempted else 'failed'
            detail = describe_webull_error(exc)
            logger.error('Webull order for %s (%s): %s', symbol, status, detail)
            self.ledger.update(trade_id, status, error=type(exc).__name__)
            return {'symbol': symbol, 'status': status, 'reason': detail}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(MainTopBullish().run(limit=args.limit), indent=2))
