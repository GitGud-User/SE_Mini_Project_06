"""Parking fee calculation (SRS: SPMS-F-060)."""
import math


def compute_fee(minutes, hourly_rate, grace_minutes=15, daily_cap=None, prepaid=0.0):
    """Return the fee to pay for a parking stay of `minutes`.

    - Free if the stay is within the grace period.
    - Otherwise every started hour is charged at `hourly_rate`.
    - Each full 24-hour block is capped at `daily_cap` (if given).
    - Any amount already paid in advance (`prepaid`) is deducted (never below 0).
    """
    if minutes < 0:
        raise ValueError("minutes cannot be negative")
    if minutes <= grace_minutes:
        return 0.0

    full_days, rest_minutes = divmod(minutes, 24 * 60)
    day_charge = 24 * hourly_rate if daily_cap is None else min(24 * hourly_rate, daily_cap)
    rest_charge = math.ceil(rest_minutes / 60) * hourly_rate
    if daily_cap is not None:
        rest_charge = min(rest_charge, daily_cap)

    fee = full_days * day_charge + rest_charge
    return float(max(fee - prepaid, 0.0))
