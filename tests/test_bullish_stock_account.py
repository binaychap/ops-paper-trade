from types import SimpleNamespace

import pytest

from app import webull_broker, webull_submitter
from app.main import Settings


@pytest.mark.parametrize('accounts,expected', [
    ([{'account_number': 'margin', 'account_id': 'first'},
      {'account_number': 'test-cash', 'account_id': 'cash-api-id'}], 'cash-api-id'),
    ([], None),
    ([{'account_number': 'margin', 'account_id': 'first'}], None),
    ([{'account_number': 'test-cash', 'account_id': 'a'},
      {'account_number': 'test-cash', 'account_id': 'b'}], None),
])
def test_stock_submission_selects_only_configured_account(monkeypatch, accounts, expected):
    orders = []
    response = SimpleNamespace(status_code=200, json=lambda: accounts)
    client = SimpleNamespace(account_v2=SimpleNamespace(get_account_list=lambda: response))
    monkeypatch.setattr(webull_broker, 'get_trade_client', lambda: client)
    module = SimpleNamespace(get_account_id=webull_broker.get_account_id,
                             buy_stock=lambda **kw: orders.append(kw) or {})
    monkeypatch.setattr(webull_submitter, '_load_webull_stock_module', lambda: module)
    settings = Settings(_env_file=None, DRY_RUN=False, NEXT_DAY_EXIT_ENABLED=False,
                        BULLISH_STOCK_ACCOUNT_NUMBER=' test-cash ')
    decision = dict(action='buy', symbol='AAPL', notional_usd=250)
    if expected:
        webull_submitter.submit_paper_order(decision, settings, 'fp')
        assert orders[0]['account_id'] == expected
    else:
        with pytest.raises(ValueError, match='exactly one'):
            webull_submitter.submit_paper_order(decision, settings, 'fp')
        assert not orders


def test_missing_stock_account_blocks_lookup(monkeypatch):
    monkeypatch.setattr(webull_submitter, '_load_webull_stock_module', lambda: object())
    settings = Settings(_env_file=None, DRY_RUN=False, BULLISH_STOCK_ACCOUNT_NUMBER='')
    with pytest.raises(ValueError, match='BULLISH_STOCK_ACCOUNT_NUMBER'):
        webull_submitter.submit_paper_order(dict(action='buy'), settings, 'fp')
