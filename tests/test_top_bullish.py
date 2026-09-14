import importlib.util
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.bullish_ledger import BullishLedger
from app.webull_quotes import current_stock_quote

spec = importlib.util.spec_from_file_location('app.main_top_bullish', Path(__file__).parents[1] / 'app/main-top-bullish.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ITEM = {'symbol': 'AAPL', 'total_premium': 25545729.7, 'trade_count': 1277}


def runner(tmp_path, *, dry_run=True, items=None, buy=None):
    settings = SimpleNamespace(database_path=str(tmp_path / 'test.sqlite3'), dry_run=dry_run, max_notional_usd=250)
    stock = SimpleNamespace(get_account_id=lambda: 'fake-account', buy_stock=buy)
    loader = Mock(return_value=stock)
    feed = SimpleNamespace(fetch=lambda limit: items or [ITEM])
    quote = Mock(return_value={'price': 200, 'last_trade_time': 1, 'source': 'fake'})
    return module.MainTopBullish(settings=settings, feed=feed, stock_loader=loader, quote_provider=quote)


def test_dry_run_persists_and_skips_symbol(tmp_path):
    bot = runner(tmp_path)
    result = bot.run()[0]
    assert result['status'] == 'dry_run'
    assert result['order']['stop_price'] == 190
    assert result['order']['target_price'] == 220
    assert bot.run()[0]['status'] == 'skipped'
    bot.stock_loader.assert_not_called()
    bot.quote_provider.assert_called_once_with('AAPL')


def test_submission_tracking_saved_before_broker_and_duplicate_id(tmp_path):
    def buy_stock(**kwargs):
        kwargs['before_submit']({'entry_id': 'fake-entry'})
        with sqlite3.connect(tmp_path / 'test.sqlite3') as conn:
            row = conn.execute('SELECT status, tracking_json FROM top_bullish_trades').fetchone()
        assert row[0] == 'submitting'
        assert 'fake-entry' in row[1]
        assert kwargs['quantity'] == 1
        assert kwargs['entry_price'] == 200
        return {'entry_id': 'fake-entry'}
    bot = runner(tmp_path, dry_run=False, buy=buy_stock, items=[{**ITEM, 'id': 'same'}])
    assert bot.run()[0]['status'] == 'submitted'
    bot.feed = SimpleNamespace(fetch=lambda limit: [{**ITEM, 'symbol': 'MSFT', 'id': 'same'}])
    assert bot.run()[0]['status'] == 'skipped'


def test_timeout_is_not_retried(tmp_path):
    def buy_stock(**kwargs):
        kwargs['before_submit']({'entry_id': 'fake-entry'})
        raise TimeoutError()
    bot = runner(tmp_path, dry_run=False, buy=buy_stock)
    assert bot.run()[0]['status'] == 'submission_unknown'
    assert bot.run()[0]['status'] == 'skipped'
    bot.stock_loader.assert_called_once()


def test_quote_failure_and_budget_do_not_submit(tmp_path):
    bot = runner(tmp_path, dry_run=False)
    bot.quote_provider.side_effect = ValueError('stale')
    assert bot.run()[0]['status'] == 'skipped'
    bot.quote_provider.side_effect = None
    bot.quote_provider.return_value = {'price': 300}
    assert bot.run()[0]['reason'] == 'One share exceeds MAX_NOTIONAL_USD'
    bot.stock_loader.assert_not_called()


def test_atomic_claim(tmp_path):
    ledger = BullishLedger(str(tmp_path / 'race.sqlite3'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ledger.claim('same', ITEM, {}), range(2)))
    assert sorted(results) == [False, True]


@pytest.mark.parametrize('price,age', [(200, 0), (0, 0), (float('nan'), 0), (200, 301), (200, -30)])
def test_quote_validation(price, age):
    now = datetime.now(UTC)
    response = SimpleNamespace(status_code=200, json=lambda: [
        {'symbol': 'AAPL', 'price': price, 'last_trade_time': (now.timestamp() - age) * 1000},
    ])
    client = SimpleNamespace(market_data=SimpleNamespace(get_snapshot=lambda *a, **kw: response))
    if price == 200 and age == 0:
        assert current_stock_quote('AAPL', data_client=client, now=now)['price'] == 200
    else:
        with pytest.raises(ValueError):
            current_stock_quote('AAPL', data_client=client, now=now)


def test_quote_error_is_actionable_without_exposing_sdk_errors(tmp_path):
    from app.webull_quotes import QuoteError
    bot = runner(tmp_path, dry_run=False)
    bot.quote_provider.side_effect = QuoteError('Stock quote is stale: maximum 300 seconds')
    assert bot.run()[0]['reason'] == 'Quote unavailable: Stock quote is stale: maximum 300 seconds'
    bot.quote_provider.side_effect = RuntimeError('private SDK response')
    assert bot.run()[0]['reason'] == 'Quote unavailable: RuntimeError'
    bot.stock_loader.assert_not_called()


def test_sdk_failure_logs_status_and_redacts_secret(tmp_path, monkeypatch, caplog):
    from webull.core.exception.exceptions import ServerException
    monkeypatch.setenv('WEBULL_APP_SECRET', 'test-private-secret')
    bot = runner(tmp_path, dry_run=False)
    bot.quote_provider.side_effect = ServerException(
        'NO_PERMISSION', 'Denied test-private-secret x-signature=private-signature',
        http_status=403,
    )
    result = bot.run()[0]
    assert 'HTTP=403' in result['reason']
    assert 'code=NO_PERMISSION' in caplog.text
    assert 'Webull quote for AAPL failed' in caplog.text
    for output in (result['reason'], caplog.text):
        assert 'test-private-secret' not in output
        assert 'private-signature' not in output
    bot.stock_loader.assert_not_called()
