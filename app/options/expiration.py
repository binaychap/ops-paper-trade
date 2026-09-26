"""Pure expiration selection shared by option-chain callers."""
from datetime import date


def resolve_option_expiry(requested_expiry, available_expiries):
    """Use the first listed expiry on or after the requested minimum date."""
    requested = date.fromisoformat(requested_expiry)
    available = sorted({date.fromisoformat(value) for value in available_expiries})
    for expiry in available:
        if expiry >= requested:
            return expiry.isoformat()
    raise ValueError(f'No listed option expiration on or after {requested_expiry}')
