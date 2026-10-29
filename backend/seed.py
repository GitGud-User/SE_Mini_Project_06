"""Create demo data:  python -m backend.seed

Adds one parking lot with two levels, 34 slots and tariffs, plus the demo
admin/attendant accounts. Safe to run more than once.
"""
from sqlalchemy import select

from backend import auth
from backend.db import SessionLocal, init_db
from backend.lots import (ACCESSIBLE, EV, FOUR_WHEELER, TWO_WHEELER, FREE, Level, ParkingLot, Slot, Tariff)

DEMO_LOT = "City Centre Parking"

LAYOUT = [  # (floor, level name, [(prefix, slot type, count)])
    (0, "Ground", [("A", FOUR_WHEELER, 12), ("E", EV, 4), ("H", ACCESSIBLE, 2)]),
    (1, "Level 1", [("B", FOUR_WHEELER, 8), ("M", TWO_WHEELER, 8)]),
]

TARIFFS = [  # (vehicle type, hourly rate, grace minutes, daily cap)
    (TWO_WHEELER, 10.0, 15, 80.0),
    (FOUR_WHEELER, 30.0, 15, 250.0),
    (EV, 40.0, 15, 300.0),
    (ACCESSIBLE, 15.0, 30, 100.0),
]


def seed() -> ParkingLot:
    init_db()
    with SessionLocal() as db:
        auth.seed_default_users(db)
        lot = db.scalar(select(ParkingLot).where(ParkingLot.name == DEMO_LOT))
        if lot is not None:
            return lot
        lot = ParkingLot(name=DEMO_LOT, address="MG Road, Bengaluru")
        db.add(lot)
        db.flush()
        for floor, level_name, groups in LAYOUT:
            level = Level(lot_id=lot.id, floor_no=floor, name=level_name)
            db.add(level)
            db.flush()
            for prefix, slot_type, count in groups:
                for n in range(1, count + 1):
                    db.add(Slot(lot_id=lot.id, level_id=level.id, code=f"{prefix}-{n}", slot_type=slot_type,
                                status=FREE, distance_to_entry=floor * 100 + n))
        for vtype, rate, grace, cap in TARIFFS:
            db.add(Tariff(lot_id=lot.id, vehicle_type=vtype, hourly_rate=rate, grace_minutes=grace, daily_cap=cap))
        db.commit()
        return lot


if __name__ == "__main__":
    created = seed()
    print(f"Demo lot ready: '{created.name}' (id={created.id}).  Admin: admin@spms.local / admin123")
