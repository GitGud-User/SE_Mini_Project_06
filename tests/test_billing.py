import pytest

from backend.billing import compute_fee


def test_free_within_grace_period():
    assert compute_fee(14, hourly_rate=20) == 0.0


def test_started_hour_is_charged():
    assert compute_fee(61, hourly_rate=20) == 40.0  # 2 started hours


def test_daily_cap_applies():
    assert compute_fee(26 * 60, hourly_rate=20, daily_cap=200) == 240.0  # 200 cap + 2 h


def test_prepaid_amount_is_deducted():
    assert compute_fee(120, hourly_rate=20, prepaid=30) == 10.0


def test_negative_minutes_rejected():
    with pytest.raises(ValueError):
        compute_fee(-5, hourly_rate=20)
