"""Volume data for the scalability check (SPMS-NF-005): 10 lots, 5,000 slots, 1,000 reservations a day.

    python -m backend.volume_seed      # adds the volume data to the local database (for load tests)
"""
import datetime as dt

from backend import auth
from backend.db import SessionLocal, init_db
from backend.lots import FOUR_WHEELER, Level, ParkingLot, Slot
from backend.reservations import CONFIRMED, Reservation, Vehicle


def seed_volume(db, lots: int = 10, slots_per_lot: int = 500, reservations: int = 1000) -> dict:
    """Bulk-insert lots, slots, one driver with vehicles, and reservations spread over the next day."""
    now = auth.utcnow().replace(second=0, microsecond=0)
    driver = auth.User(name="Volume Driver", email=f"volume{now:%H%M%S%f}@spms.local", phone=f"8{now:%H%M%S%f}"[:10],
                       password_hash="x", role=auth.DRIVER)
    db.add(driver)
    db.flush()
    lot_rows = [ParkingLot(name=f"Volume Lot {now:%H%M%S}-{i}", address="") for i in range(lots)]
    db.add_all(lot_rows)
    db.flush()
    levels = [Level(lot_id=lot.id, floor_no=0, name="Ground") for lot in lot_rows]
    db.add_all(levels)
    db.flush()
    db.add_all([Slot(lot_id=lv.lot_id, level_id=lv.id, code=f"V-{n}", slot_type=FOUR_WHEELER, status="FREE",
                     distance_to_entry=n) for lv in levels for n in range(1, slots_per_lot + 1)])
    vehicles = [Vehicle(user_id=driver.id, plate=f"KA{i % 90 + 10:02d}VL{i:04d}", vehicle_type=FOUR_WHEELER)
                for i in range(reservations)]
    db.add_all(vehicles)
    db.flush()
    slot_ids = {lot.id: [s.id for s in db.query(Slot.id).filter(Slot.lot_id == lot.id).limit(100)] for lot in lot_rows}
    rows = []
    for i, v in enumerate(vehicles):
        lot = lot_rows[i % lots]
        start = now + dt.timedelta(hours=1 + (i // (lots * 100)) * 2)
        rows.append(Reservation(code=f"V-{i:06d}", user_id=driver.id, vehicle_id=v.id, lot_id=lot.id,
                                slot_id=slot_ids[lot.id][(i // lots) % 100], plate=v.plate, vehicle_type=FOUR_WHEELER,
                                start=start, end=start + dt.timedelta(hours=1), status=CONFIRMED, created_at=now))
    db.add_all(rows)
    db.commit()
    return {"lots": lots, "slots": lots * slots_per_lot, "reservations": reservations,
            "lot_ids": [lot.id for lot in lot_rows]}


if __name__ == "__main__":
    init_db()
    with SessionLocal() as session:
        print(seed_volume(session))
