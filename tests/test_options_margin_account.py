from types import SimpleNamespace

import pytest

from app.strategy_settings import options_margin_account_id
from app import webull_broker


def test_selects_exact_account_instead_of_first(monkeypatch):
    accounts = [{'account_number': 'other', 'account_id': 'first'},
                {'account_number': 'test-margin', 'account_id': 'selected'}]
    response = SimpleNamespace(status_code=200, json=lambda: accounts)
    monkeypatch.setattr(webull_broker, 'get_trade_client', lambda: SimpleNamespace(
        account_v2=SimpleNamespace(get_account_list=lambda: response)))
    settings = SimpleNamespace(options_margin_account_number='test-margin')
    assert options_margin_account_id(webull_broker, settings) == 'selected'


@pytest.mark.parametrize('accounts', [[], [{'account_number': 'other', 'account_id': 'first'}],
    [{'account_number': 'test-margin', 'account_id': 'a'}, {'account_number': 'test-margin', 'account_id': 'b'}]])
def test_missing_or_ambiguous_account_rejected(monkeypatch, accounts):
    response = SimpleNamespace(status_code=200, json=lambda: accounts)
    monkeypatch.setattr(webull_broker, 'get_trade_client', lambda: SimpleNamespace(
        account_v2=SimpleNamespace(get_account_list=lambda: response)))
    with pytest.raises(ValueError, match='exactly one'):
        options_margin_account_id(webull_broker, SimpleNamespace(options_margin_account_number='test-margin'))


def test_missing_configuration_does_not_query_broker():
    with pytest.raises(ValueError, match='OPTIONS_MARGIN_ACCOUNT_NUMBER'):
        options_margin_account_id(object(), SimpleNamespace())
