import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.webull_quotes import QuoteError, current_option_ask


NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)
SYMBOL = 'AAPL260918P00100000'


def quote_client(rows, status=200):
    def snapshot(symbol, category):
        assert (symbol, category) == (SYMBOL, 'US_OPTION')
        return SimpleNamespace(status_code=status, json=lambda: rows)
    return SimpleNamespace(option_market_data=SimpleNamespace(get_option_snapshot=snapshot))


def test_current_option_ask_uses_ask_not_last_trade():
    row = {'symbol': SYMBOL, 'ask': '2.00', 'price': '1.75', 'quote_time': NOW.timestamp() * 1000}
    result = current_option_ask(SYMBOL, data_client=quote_client([row]), now=NOW)
    assert result['price'] == 2.0


@pytest.mark.parametrize('change', [
    {'ask': 0}, {'ask': -1}, {'ask': 'NaN'}, {'ask': 'inf'}, {'ask': True},
    {'quote_time': (NOW.timestamp() - 61) * 1000},
    {'quote_time': (NOW.timestamp() + 6) * 1000},
    {'quote_time': None}, {'symbol': 'WRONG'},
])
def test_invalid_option_quotes_are_rejected(change):
    row = {'symbol': SYMBOL, 'ask': '2.00', 'quote_time': NOW.timestamp() * 1000}
    row.update(change)
    with pytest.raises(QuoteError):
        current_option_ask(SYMBOL, data_client=quote_client([row]), now=NOW)


@pytest.mark.parametrize('rows,status', [([], 200), ({}, 200), ([], 403)])
def test_missing_or_unavailable_option_quote(rows, status):
    with pytest.raises(QuoteError):
        current_option_ask(SYMBOL, data_client=quote_client(rows, status), now=NOW)


def load_builder():
    path = Path(__file__).resolve().parents[1] / 'app/webull-buy-combo-option.py'
    spec = importlib.util.spec_from_file_location('bearish_premium_builder_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_put_bracket_quotes_selected_contract_and_submits_20_10_exits(monkeypatch):
    module = load_builder()
    calls = []

    def contract(symbol, expiration, strike, **kwargs):
        assert kwargs == {'option_type': 'PUT'}
        return '2026-09-18', 100.0, SYMBOL

    def ask(symbol):
        assert symbol == SYMBOL
        return {'price': 2.0}

    def place_order(account, orders, **kwargs):
        calls.extend(orders)
        return SimpleNamespace(status_code=200, json=lambda: {'order_id': 'test'})

    monkeypatch.setattr(module, '_find_valid_contract', contract)
    monkeypatch.setattr('app.webull_quotes.current_option_ask', ask)
    client = SimpleNamespace(order_v3=SimpleNamespace(place_order=place_order))
    module.buy_put_with_bracket('test', 'AAPL', 103, '2026-09-21', 1, trade_client=client)
    entry, profit, stop = calls
    assert (entry['side'], entry['position_intent'], entry['order_type']) == ('BUY', 'BUY_TO_OPEN', 'LIMIT')
    assert entry['limit_price'] == '2.00'
    assert profit['limit_price'] == '2.40'
    assert stop['stop_price'] == '1.80'
    assert profit['side'] == stop['side'] == 'SELL'
    assert all(order['legs'][0]['option_type'] == 'PUT' for order in calls)
    assert all(order['legs'][0]['strike_price'] == '100.00' for order in calls)


def test_quote_failure_prevents_order_submission(monkeypatch):
    module = load_builder()
    monkeypatch.setattr(module, '_find_valid_contract', lambda *a, **k: ('2026-09-18', 100, SYMBOL))

    def unavailable(symbol):
        raise QuoteError('stale')

    monkeypatch.setattr('app.webull_quotes.current_option_ask', unavailable)
    with pytest.raises(QuoteError, match='stale'):
        module.buy_put_with_bracket('test', 'AAPL', 100, '2026-09-18', 1, trade_client=object())


def test_contract_lookup_excludes_calls(monkeypatch):
    module = load_builder()
    rows = [
        {'option_type': 'PUT', 'expiration': '2026-09-18', 'strike': 100, 'symbol': SYMBOL},
        {'option_type': 'CALL', 'expiration': '2026-09-18', 'strike': 100, 'symbol': 'CALL'},
    ]
    response = SimpleNamespace(status_code=200, json=lambda: rows)
    api = SimpleNamespace(add_endpoint=lambda *a: None, set_stream_logger=lambda **k: None)
    data = SimpleNamespace(instrument=SimpleNamespace(get_option_contracts=lambda **k: response))
    monkeypatch.setenv('WEBULL_APP_KEY', 'test')
    monkeypatch.setenv('WEBULL_APP_SECRET', 'test')
    monkeypatch.setattr(module, 'ApiClient', lambda *a: api)
    monkeypatch.setattr(module, 'DataClient', lambda *a: data)
    assert module._find_valid_contract('AAPL', '2026-09-18', 100, option_type='PUT') == (
        '2026-09-18', 100.0, SYMBOL,
    )
