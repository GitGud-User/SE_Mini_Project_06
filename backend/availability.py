"""Free-slot counting for live availability (SRS: SPMS-F-020)."""
from collections import Counter

FREE = "FREE"
MAINTENANCE = "MAINTENANCE"


def count_free_slots(slots):
    """Count FREE slots per slot type.

    `slots` is a list of dicts like {"code": "B1-14", "type": "FOUR_WHEELER", "status": "FREE"}.
    Slots under maintenance or occupied/reserved are not counted.
    """
    return dict(Counter(s["type"] for s in slots if s["status"] == FREE))


def occupancy_percent(slots):
    """Percentage of usable (non-maintenance) slots that are not free."""
    usable = [s for s in slots if s["status"] != MAINTENANCE]
    if not usable:
        return 0.0
    taken = sum(1 for s in usable if s["status"] != FREE)
    return round(100 * taken / len(usable), 1)
