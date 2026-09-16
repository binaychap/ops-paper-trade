"""Shared strategy exit percentages; values are percentages, not fractions."""
from pydantic import Field
from pydantic_settings import BaseSettings


class StrategyExitSettings(BaseSettings):
    bullish_stock_account_number: str = Field(default="", alias="BULLISH_STOCK_ACCOUNT_NUMBER", repr=False)
    options_margin_account_number: str = Field(default="", alias="OPTIONS_MARGIN_ACCOUNT_NUMBER", repr=False)
    bullish_profit_percent: float = Field(default=10, gt=0, allow_inf_nan=False, alias='BULLISH_PROFIT_PERCENT')
    bullish_stop_loss_percent: float = Field(default=5, gt=0, lt=100, allow_inf_nan=False, alias='BULLISH_STOP_LOSS_PERCENT')
    bearish_profit_percent: float = Field(default=20, gt=0, allow_inf_nan=False, alias='BEARISH_PROFIT_PERCENT')
    bearish_stop_loss_percent: float = Field(default=10, gt=0, lt=100, allow_inf_nan=False, alias='BEARISH_STOP_LOSS_PERCENT')
    iron_condor_profit_percent: float = Field(default=10, gt=0, lt=100, allow_inf_nan=False, alias='IRON_CONDOR_PROFIT_PERCENT')
    iron_condor_stop_loss_percent: float = Field(default=5, gt=0, allow_inf_nan=False, alias='IRON_CONDOR_STOP_LOSS_PERCENT')


def exit_percentages(settings, strategy):
    """Support injected lightweight settings while sharing production defaults."""
    names = (f'{strategy}_profit_percent', f'{strategy}_stop_loss_percent')
    return tuple(getattr(settings, name, StrategyExitSettings.model_fields[name].default) for name in names)


def options_margin_account_id(module, settings):
    account_number = getattr(settings, 'options_margin_account_number', '').strip()
    if not account_number:
        raise ValueError('Set OPTIONS_MARGIN_ACCOUNT_NUMBER before submitting bearish or iron-condor orders')
    return module.get_account_id(account_number=account_number)


def bullish_stock_account_id(module, settings):
    account_number = getattr(settings, 'bullish_stock_account_number', '').strip()
    if not account_number:
        raise ValueError('Set BULLISH_STOCK_ACCOUNT_NUMBER before submitting bullish stock orders')
    return module.get_account_id(account_number=account_number)
