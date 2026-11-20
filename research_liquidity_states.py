"""Conservative research-only classification of liquidity observations.

This module does NOT authorize orders and is not wired into live/paper services.
"""
from math import isfinite


CONFIRMED_POSITIVE = "LIQUIDITY_POSITIVE_OBSERVED"
ZERO_UNVERIFIED = "LIQUIDITY_ZERO_UNVERIFIED"
UNKNOWN = "LIQUIDITY_UNKNOWN"
INVALID = "LIQUIDITY_INVALID"


def classify_liquidity(status, liquidity_usd, price_usd):
    """Return a descriptive, non-trading classification of *one* observation.

    Observed positive liquidity is not an independent on-chain confirmation,
    and is never sufficient proof that an executable exit is possible.
    """
    try:
        price = float(price_usd) if price_usd is not None else None
        lp = float(liquidity_usd) if liquidity_usd is not None else None
    except (ValueError, TypeError, OverflowError):
        return INVALID

    if price is None or not isfinite(price) or price <= 0:
        return UNKNOWN

    if status == "MARKET_DATA_UNAVAILABLE" or status == "LIQUIDITY_UNKNOWN":
        return UNKNOWN

    if status != "QUOTE_MARKET_OBSERVED":
        return UNKNOWN

    if lp is None:
        return UNKNOWN
    if not isfinite(lp) or lp < 0:
        return INVALID
    if lp == 0:
        return ZERO_UNVERIFIED
    return CONFIRMED_POSITIVE


def allows_research_entry(status, liquidity_usd, price_usd):
    """Placeholder conservative guard only: never grant trade approval.

    No market observation by itself is enough to approve a new position.
    """
    return False
