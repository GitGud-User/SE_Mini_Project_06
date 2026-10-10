from backend.availability import count_free_slots, occupancy_percent

SLOTS = [
    {"code": "A1", "type": "FOUR_WHEELER", "status": "FREE"},
    {"code": "A2", "type": "FOUR_WHEELER", "status": "OCCUPIED"},
    {"code": "A3", "type": "TWO_WHEELER", "status": "FREE"},
    {"code": "A4", "type": "TWO_WHEELER", "status": "MAINTENANCE"},
    {"code": "A5", "type": "EV", "status": "RESERVED"},
]


def test_count_free_slots_by_type():
    assert count_free_slots(SLOTS) == {"FOUR_WHEELER": 1, "TWO_WHEELER": 1}


def test_maintenance_slots_not_counted_as_free():
    assert "EV" not in count_free_slots(SLOTS)


def test_occupancy_percent_ignores_maintenance():
    assert occupancy_percent(SLOTS) == 50.0  # 2 of 4 usable slots taken


def test_occupancy_empty_lot():
    assert occupancy_percent([]) == 0.0
