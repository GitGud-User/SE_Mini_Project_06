"""Gate operations: vehicle entry, slot allocation, exit and fee.

SRS: SPMS-F-050 (log entry with plate), F-051 (allocate nearest suitable slot),
F-052 (log exit and free the slot), F-053 (validate reservation QR at the gate),
F-060 (compute fee using the lot tariff and billing.compute_fee).
"""
import datetime as dt
import math
import re

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend import auth
from backend.auth import ADMIN, ATTENDANT, require_roles
from backend.billing import compute_fee
from backend.db import Base, get_db
from backend.lots import FREE, OCCUPIED, SLOT_TYPES, Slot, find_best_free_slot, get_lot_or_404, get_tariff

OPEN, CLOSED = "OPEN", "CLOSED"
QR_WINDOW_MINUTES = 15
PLATE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$")  # e.g. KA01AB1234


def _now() -> dt.datetime:
    """Current UTC time (naive). Tests replace this to simulate time passing."""
    return auth.utcnow()


class ParkingSession(Base):
    __tablename__ = "parking_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket: Mapped[str] = mapped_column(String(20), unique=True, default="")
    lot_id: Mapped[int] = mapped_column(ForeignKey("parking_lots.id"))
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id"))
    plate: Mapped[str] = mapped_column(String(15), index=True)
    vehicle_type: Mapped[str] = mapped_column(String(20))
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime)
    exit_time: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=OPEN)


# ---------- schemas ----------
def _clean_plate(v: str) -> str:
    v = re.sub(r"[\s-]", "", v).upper()
    if not PLATE_RE.fullmatch(v):
        raise ValueError("plate must look like KA01AB1234")
    return v


class EntryIn(BaseModel):
    lot_id: int
    plate: str
    vehicle_type: str = "FOUR_WHEELER"
    reservation_qr: str | None = None

    _plate = field_validator("plate")(classmethod(lambda cls, v: _clean_plate(v)))

    @field_validator("vehicle_type")
    @classmethod
    def _type(cls, v):
        v = v.strip().upper()
        if v not in SLOT_TYPES:
            raise ValueError(f"vehicle_type must be one of {', '.join(SLOT_TYPES)}")
        return v


class ExitIn(BaseModel):
    plate: str

    _plate = field_validator("plate")(classmethod(lambda cls, v: _clean_plate(v)))


class SessionOut(BaseModel):
    ticket: str
    lot_id: int
    slot_code: str
    plate: str
    vehicle_type: str
    entry_time: dt.datetime
    exit_time: dt.datetime | None
    minutes: int | None
    fee: float | None
    status: str


def _session_out(db: Session, s: ParkingSession) -> SessionOut:
    slot = db.get(Slot, s.slot_id)
    minutes = None
    if s.exit_time is not None:
        minutes = math.ceil((s.exit_time - s.entry_time).total_seconds() / 60)
    return SessionOut(ticket=s.ticket, lot_id=s.lot_id, slot_code=slot.code if slot else "?", plate=s.plate,
                      vehicle_type=s.vehicle_type, entry_time=s.entry_time, exit_time=s.exit_time,
                      minutes=minutes, fee=s.fee, status=s.status)


# ---------- reservation QR (F-053) ----------
def make_reservation_qr(lot_id: int, plate: str, start: dt.datetime, slot_id: int | None = None) -> str:
    """Signed QR payload for a reservation. Sprint 2's reservation module will call this."""
    payload = {"kind": "reservation", "lot": lot_id, "plate": _clean_plate(plate),
               "start": start.isoformat(), "slot": slot_id}
    return jwt.encode(payload, auth.SECRET_KEY, algorithm=auth.ALGORITHM)


def validate_reservation_qr(qr: str, lot_id: int, plate: str) -> dict:
    """Check signature, lot, plate and the +/- 15 minute arrival window. Raises 400 if invalid."""
    try:
        data = jwt.decode(qr, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid reservation QR")
    if data.get("kind") != "reservation" or data.get("lot") != lot_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "QR is not for this parking lot")
    if data.get("plate") != plate:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "QR plate does not match the vehicle")
    start = dt.datetime.fromisoformat(data["start"])
    if abs((_now() - start).total_seconds()) > QR_WINDOW_MINUTES * 60:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "QR is outside the allowed arrival window")
    return data


# ---------- routes ----------
router = APIRouter(prefix="/api/v1/gate", tags=["gate"])
staff = require_roles(ADMIN, ATTENDANT)


@router.post("/entry", response_model=SessionOut, status_code=201, dependencies=[Depends(staff)])
def vehicle_entry(data: EntryIn, db: Session = Depends(get_db)):
    """SPMS-F-050/F-051/F-053: log a vehicle entering and allocate the nearest suitable slot."""
    lot = get_lot_or_404(db, data.lot_id)
    if not lot.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This parking lot is closed")
    if db.scalar(select(ParkingSession).where(ParkingSession.plate == data.plate, ParkingSession.status == OPEN)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This vehicle is already inside")

    slot = None
    if data.reservation_qr:
        qr = validate_reservation_qr(data.reservation_qr, lot.id, data.plate)
        if qr.get("slot"):
            slot = db.get(Slot, qr["slot"])
    if slot is None or slot.status not in (FREE, "RESERVED"):
        slot = find_best_free_slot(db, lot.id, data.vehicle_type)
    if slot is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Lot is full for {data.vehicle_type}")

    slot.status = OCCUPIED
    session = ParkingSession(lot_id=lot.id, slot_id=slot.id, plate=data.plate,
                             vehicle_type=data.vehicle_type, entry_time=_now(), status=OPEN)
    db.add(session)
    db.flush()
    session.ticket = f"T-{session.id:06d}"
    db.commit()
    return _session_out(db, session)


@router.post("/exit", response_model=SessionOut, dependencies=[Depends(staff)])
def vehicle_exit(data: ExitIn, db: Session = Depends(get_db)):
    """SPMS-F-052/F-060: log exit, compute the fee with the lot tariff and free the slot."""
    session = db.scalar(select(ParkingSession).where(ParkingSession.plate == data.plate,
                                                     ParkingSession.status == OPEN))
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open parking session for this vehicle")
    session.exit_time = _now()
    minutes = math.ceil((session.exit_time - session.entry_time).total_seconds() / 60)
    tariff = get_tariff(db, session.lot_id, session.vehicle_type)
    session.fee = compute_fee(minutes, tariff.hourly_rate, tariff.grace_minutes, tariff.daily_cap)
    session.status = CLOSED
    slot = db.get(Slot, session.slot_id)
    if slot is not None:
        slot.status = FREE
    db.commit()
    return _session_out(db, session)


@router.get("/sessions", response_model=list[SessionOut], dependencies=[Depends(staff)])
def list_sessions(lot_id: int | None = None, only_open: bool = False, db: Session = Depends(get_db)):
    """Recent parking sessions for the attendant screen."""
    query = select(ParkingSession).order_by(ParkingSession.id.desc()).limit(100)
    if lot_id is not None:
        query = query.where(ParkingSession.lot_id == lot_id)
    if only_open:
        query = query.where(ParkingSession.status == OPEN)
    return [_session_out(db, s) for s in db.scalars(query)]
