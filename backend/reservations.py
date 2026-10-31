"""Vehicles, reservations and slot allocation.

SRS: SPMS-F-030 (reserve a slot for a registered vehicle, 30 min - 24 h, up to 7 days ahead, advance paid,
QR issued), SPMS-F-031 (no double booking: no two active reservations/sessions overlap on one slot, even
under concurrent requests), SPMS-F-040 (auto-allocate the nearest slot matching vehicle type and
accessibility need).
The gate uses `allocate_walk_in()` so walk-in cars never take a slot that is reserved for the next hour.
"""
import datetime as dt
import math
import threading

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, and_, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend import auth
from backend.auth import ADMIN, ATTENDANT, DRIVER, User, get_current_user, require_roles
from backend.billing import compute_fee
from backend.db import Base, get_db
from backend.lots import (ACCESSIBLE, EV, FOUR_WHEELER, MAINTENANCE, OCCUPIED, SLOT_TYPES, TWO_WHEELER, Slot,
                          get_lot_or_404, get_tariff)

MIN_MINUTES, MAX_MINUTES, MAX_DAYS_AHEAD = 30, 24 * 60, 7
NO_SHOW_MINUTES = 15          # a CONFIRMED reservation stops blocking its slot 15 min after start (F-033)
WALK_IN_BUFFER_MINUTES = 60   # walk-ins never get a slot reserved within the next hour

CONFIRMED, CHECKED_IN, COMPLETED, CANCELLED, NO_SHOW = "CONFIRMED", "CHECKED_IN", "COMPLETED", "CANCELLED", "NO_SHOW"

# Allocation + insert happen under this lock, so two requests can never pick the same free slot (F-031).
# On PostgreSQL the slot row is also locked with SELECT ... FOR UPDATE.
ALLOC_LOCK = threading.Lock()


def _now() -> dt.datetime:
    return auth.utcnow()


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plate: Mapped[str] = mapped_column(String(15), unique=True)
    vehicle_type: Mapped[str] = mapped_column(String(20))
    needs_accessible: Mapped[bool] = mapped_column(Boolean, default=False)


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), default="", index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    lot_id: Mapped[int] = mapped_column(ForeignKey("parking_lots.id"))
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"), index=True)
    plate: Mapped[str] = mapped_column(String(15), index=True)
    vehicle_type: Mapped[str] = mapped_column(String(20))
    start: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    end: Mapped[dt.datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(15), default=CONFIRMED, index=True)
    advance_amount: Mapped[float] = mapped_column(Float, default=0.0)
    advance_ref: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    # used by later features: gate check-in, cancellation/refund (F-032), reminders (F-072)
    session_ticket: Mapped[str | None] = mapped_column(String(20), nullable=True)
    refund_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------- allocation (F-040) ----------
def compatible_types(vehicle_type: str, needs_accessible: bool = False) -> list[str]:
    """Slot types a vehicle may use, best first."""
    if vehicle_type == ACCESSIBLE or (needs_accessible and vehicle_type == FOUR_WHEELER):
        return [ACCESSIBLE, FOUR_WHEELER] if vehicle_type == FOUR_WHEELER else [ACCESSIBLE]
    return [vehicle_type]


def blocking(now: dt.datetime):
    """SQL condition: reservations that still hold their slot at time `now`."""
    return or_(Reservation.status == CHECKED_IN,
               and_(Reservation.status == CONFIRMED,
                    Reservation.start >= now - dt.timedelta(minutes=NO_SHOW_MINUTES)))


def reserved_slot_ids(db: Session, lot_id: int, start: dt.datetime, end: dt.datetime, now: dt.datetime,
                      ignore_id: int | None = None) -> set[int]:
    """Slots with an active reservation overlapping [start, end)."""
    query = select(Reservation.slot_id).where(Reservation.lot_id == lot_id, blocking(now),
                                              Reservation.start < end, Reservation.end > start)
    if ignore_id is not None:
        query = query.where(Reservation.id != ignore_id)
    return set(db.scalars(query))


def held_slot_ids(db: Session, lot_id: int, now: dt.datetime) -> set[int]:
    """Slots whose reservation arrival window is open now (start - 15 min .. start + 15 min), not yet checked in."""
    window = dt.timedelta(minutes=NO_SHOW_MINUTES)
    query = select(Reservation.slot_id).where(Reservation.lot_id == lot_id, Reservation.status == CONFIRMED,
                                              Reservation.start <= now + window, Reservation.start >= now - window)
    return set(db.scalars(query))


def allocate_slot(db: Session, lot_id: int, vehicle_type: str, start: dt.datetime, end: dt.datetime,
                  needs_accessible: bool = False, now: dt.datetime | None = None,
                  ignore_reservation_id: int | None = None) -> Slot | None:
    """F-040: nearest compatible slot that is not under maintenance and not reserved in [start, end).
    If the window starts within the next hour, slots that are occupied right now are skipped too."""
    now = now or _now()
    types = compatible_types(vehicle_type, needs_accessible)
    taken = reserved_slot_ids(db, lot_id, start, end, now, ignore_reservation_id)
    soon = start <= now + dt.timedelta(minutes=WALK_IN_BUFFER_MINUTES)
    query = select(Slot).where(Slot.lot_id == lot_id, Slot.slot_type.in_(types), Slot.status != MAINTENANCE)
    candidates = [s for s in db.scalars(query) if s.id not in taken and not (soon and s.status == OCCUPIED)]
    candidates.sort(key=lambda s: (types.index(s.slot_type), s.status == OCCUPIED, s.distance_to_entry, s.id))
    if not candidates:
        return None
    # PostgreSQL: lock the chosen row until commit (SQLite ignores FOR UPDATE; ALLOC_LOCK covers it)
    return db.scalar(select(Slot).where(Slot.id == candidates[0].id).with_for_update())


def allocate_walk_in(db: Session, lot_id: int, vehicle_type: str, now: dt.datetime | None = None) -> Slot | None:
    """Gate entry without reservation: nearest FREE compatible slot not reserved within the next hour."""
    now = now or _now()
    end = now + dt.timedelta(minutes=WALK_IN_BUFFER_MINUTES)
    return allocate_slot(db, lot_id, vehicle_type, now, end, now=now)


# ---------- schemas ----------
def _to_utc_naive(v: dt.datetime) -> dt.datetime:
    if v.tzinfo is not None:
        v = v.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return v.replace(second=0, microsecond=0)


class VehicleIn(BaseModel):
    plate: str
    vehicle_type: str = FOUR_WHEELER
    needs_accessible: bool = False

    @field_validator("plate")
    @classmethod
    def _plate(cls, v):
        from backend.gate import _clean_plate
        return _clean_plate(v)

    @field_validator("vehicle_type")
    @classmethod
    def _type(cls, v):
        v = v.strip().upper()
        if v not in (TWO_WHEELER, FOUR_WHEELER, EV):
            raise ValueError("vehicle_type must be TWO_WHEELER, FOUR_WHEELER or EV")
        return v


class VehicleOut(BaseModel):
    id: int
    plate: str
    vehicle_type: str
    needs_accessible: bool


class ReservationIn(BaseModel):
    vehicle_id: int
    lot_id: int
    start: dt.datetime
    end: dt.datetime

    _times = field_validator("start", "end")(classmethod(lambda cls, v: _to_utc_naive(v)))


class ReservationOut(BaseModel):
    id: int
    code: str
    lot_id: int
    slot_code: str
    plate: str
    vehicle_type: str
    start: dt.datetime
    end: dt.datetime
    status: str
    advance_amount: float
    advance_ref: str
    refund_amount: float | None
    qr_token: str | None


def reservation_out(db: Session, r: Reservation, with_qr: bool = True) -> ReservationOut:
    from backend.gate import make_reservation_qr  # gate imports this module, so import lazily
    slot = db.get(Slot, r.slot_id)
    qr = make_reservation_qr(r.lot_id, r.plate, r.start, r.slot_id, reservation_id=r.id) \
        if with_qr and r.status == CONFIRMED else None
    return ReservationOut(id=r.id, code=r.code, lot_id=r.lot_id, slot_code=slot.code if slot else "?",
                          plate=r.plate, vehicle_type=r.vehicle_type, start=r.start, end=r.end, status=r.status,
                          advance_amount=r.advance_amount, advance_ref=r.advance_ref,
                          refund_amount=r.refund_amount, qr_token=qr)


def check_window(start: dt.datetime, end: dt.datetime, now: dt.datetime) -> None:
    """F-030 time rules (raises 422)."""
    minutes = (end - start).total_seconds() / 60
    if start < now - dt.timedelta(minutes=1):
        raise HTTPException(422, "Start time is in the past")
    if start > now + dt.timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(422, f"Reservations are possible up to {MAX_DAYS_AHEAD} days ahead")
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise HTTPException(422, "A reservation must be 30 minutes to 24 hours long")


def advance_for(db: Session, lot_id: int, vehicle_type: str, start: dt.datetime, end: dt.datetime) -> float:
    """Advance = full tariff for the reserved window (no grace period); deducted from the fee at exit."""
    t = get_tariff(db, lot_id, vehicle_type)
    return compute_fee(math.ceil((end - start).total_seconds() / 60), t.hourly_rate, 0, t.daily_cap)


def create_reservation(db: Session, user: User, data: ReservationIn) -> Reservation:
    """F-030/F-031/F-040: validate, allocate and store a reservation atomically."""
    now = _now()
    vehicle = db.get(Vehicle, data.vehicle_id)
    if vehicle is None or vehicle.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehicle not found in your account")
    lot = get_lot_or_404(db, data.lot_id)
    if not lot.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This parking lot is closed")
    check_window(data.start, data.end, now)
    with ALLOC_LOCK:
        clash = db.scalar(select(Reservation.id).where(Reservation.vehicle_id == vehicle.id, blocking(now),
                                                       Reservation.start < data.end, Reservation.end > data.start))
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, "This vehicle already has a reservation in that time")
        slot = allocate_slot(db, lot.id, vehicle.vehicle_type, data.start, data.end, vehicle.needs_accessible, now)
        if slot is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No suitable slot is free for that time")
        r = Reservation(user_id=user.id, vehicle_id=vehicle.id, lot_id=lot.id, slot_id=slot.id, plate=vehicle.plate,
                        vehicle_type=vehicle.vehicle_type, start=data.start, end=data.end, status=CONFIRMED,
                        advance_amount=advance_for(db, lot.id, slot.slot_type if slot.slot_type != ACCESSIBLE
                                                   else vehicle.vehicle_type, data.start, data.end),
                        created_at=now)
        db.add(r)
        db.flush()
        r.code = f"R-{r.id:06d}"
        r.advance_ref = f"ADV-{r.id:06d}"  # advance paid (mock gateway, test mode)
        db.commit()
    return r


def get_reservation_or_404(db: Session, code_or_id: str) -> Reservation:
    r = db.scalar(select(Reservation).where(Reservation.code == code_or_id))
    if r is None and code_or_id.isdigit():
        r = db.get(Reservation, int(code_or_id))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    return r


def check_in(db: Session, reservation_id: int) -> Reservation:
    """Used by the gate when a reservation QR is scanned."""
    r = db.get(Reservation, reservation_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    if r.status != CONFIRMED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Reservation is {r.status}, not active")
    return r


def complete_for_ticket(db: Session, ticket: str) -> None:
    """Mark the reservation of a finished parking session as COMPLETED."""
    r = db.scalar(select(Reservation).where(Reservation.session_ticket == ticket))
    if r is not None and r.status == CHECKED_IN:
        r.status = COMPLETED


# ---------- routes ----------
router = APIRouter(prefix="/api/v1", tags=["reservations"])
driver_only = require_roles(DRIVER)


@router.post("/vehicles", response_model=VehicleOut, status_code=201)
def add_vehicle(data: VehicleIn, db: Session = Depends(get_db), user: User = Depends(driver_only)):
    """Register a vehicle (needed before reserving)."""
    if db.scalar(select(Vehicle).where(Vehicle.plate == data.plate)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This plate is already registered")
    v = Vehicle(user_id=user.id, plate=data.plate, vehicle_type=data.vehicle_type,
                needs_accessible=data.needs_accessible)
    db.add(v)
    db.commit()
    return VehicleOut(id=v.id, plate=v.plate, vehicle_type=v.vehicle_type, needs_accessible=v.needs_accessible)


@router.get("/vehicles", response_model=list[VehicleOut])
def my_vehicles(db: Session = Depends(get_db), user: User = Depends(driver_only)):
    rows = db.scalars(select(Vehicle).where(Vehicle.user_id == user.id).order_by(Vehicle.id))
    return [VehicleOut(id=v.id, plate=v.plate, vehicle_type=v.vehicle_type, needs_accessible=v.needs_accessible)
            for v in rows]


@router.post("/reservations", response_model=ReservationOut, status_code=201)
def reserve(data: ReservationIn, db: Session = Depends(get_db), user: User = Depends(driver_only)):
    """SPMS-F-030: reserve a slot. Returns the slot code, advance paid and a QR token for the gate."""
    return reservation_out(db, create_reservation(db, user, data))


@router.get("/reservations/mine", response_model=list[ReservationOut])
def my_reservations(db: Session = Depends(get_db), user: User = Depends(driver_only)):
    rows = db.scalars(select(Reservation).where(Reservation.user_id == user.id).order_by(Reservation.start.desc()))
    return [reservation_out(db, r) for r in rows]


@router.get("/reservations/{code}", response_model=ReservationOut)
def get_reservation(code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = get_reservation_or_404(db, code)
    if user.role == DRIVER and r.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    return reservation_out(db, r)


@router.get("/lots/{lot_id}/reservations", response_model=list[ReservationOut],
            dependencies=[Depends(require_roles(ADMIN, ATTENDANT))])
def lot_reservations(lot_id: int, db: Session = Depends(get_db)):
    """Staff view of upcoming reservations in a lot."""
    get_lot_or_404(db, lot_id)
    rows = db.scalars(select(Reservation).where(Reservation.lot_id == lot_id, Reservation.status.in_(
        [CONFIRMED, CHECKED_IN])).order_by(Reservation.start))
    return [reservation_out(db, r, with_qr=False) for r in rows]
