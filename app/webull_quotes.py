"""Validated current stock snapshots for entry pricing."""

import math
from datetime import UTC, datetime


class QuoteError(ValueError):
    """Safe, actionable quote failure suitable for display to the caller."""


def current_stock_quote(symbol, *, data_client=None, now=None, max_age_seconds=300):
    if data_client is None:
        from app.webull_broker import get_data_client
        data_client = get_data_client()
    response = data_client.market_data.get_snapshot(
        symbol, 'US_STOCK', extend_hour_required=False, overnight_required=False,
    )
    if response.status_code != 200:
        from webull.core.exception.exceptions import ServerException
        from app.webull_errors import describe_webull_error

        try:
            error = response.json()
        except ValueError:
            error = {}
        if not isinstance(error, dict):
            error = {}
        raise QuoteError(describe_webull_error(ServerException(
            error.get('error_code', 'UNKNOWN'), error.get('message', ''),
            http_status=response.status_code,
        )))
    try:
        payload = response.json()
    except ValueError as exc:
        raise QuoteError('Webull snapshot response is not valid JSON') from exc
    if not isinstance(payload, list):
        raise QuoteError('Unexpected Webull snapshot response: expected a list')
    matches = [row for row in payload if isinstance(row, dict) and row.get('symbol') == symbol]
    if len(matches) != 1:
        raise QuoteError(f'Missing or ambiguous stock snapshot for {symbol}')
    row = matches[0]
    try:
        price = float(row['price'])
        timestamp = float(row['last_trade_time']) / 1000
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise QuoteError('Stock snapshot has missing or invalid price/last_trade_time') from exc
    age = (now or datetime.now(UTC)).timestamp() - timestamp
    if not math.isfinite(price) or price <= 0:
        raise QuoteError('Invalid stock quote price')
    if not math.isfinite(age):
        raise QuoteError('Invalid stock quote timestamp')
    if age < -5:
        raise QuoteError(f'Stock quote is future-dated by {-age:.0f} seconds')
    if age > max_age_seconds:
        raise QuoteError(
            f'Stock quote is stale: last trade was {age:.0f} seconds ago '
            f'(maximum {max_age_seconds} seconds); market may be closed or data delayed'
        )
    return {'price': price, 'last_trade_time': row['last_trade_time'], 'source': 'webull'}
