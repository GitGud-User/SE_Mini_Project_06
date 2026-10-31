"""Change, cancel and expire reservations.

SRS: SPMS-F-032 (modify or cancel before the start time; refund 100% of the advance if cancelled at least
1 hour before start, otherwise 50%; the slot is released immediately) and SPMS-F-033 (no-show: a
reservation whose vehicle has not entered within 15 minutes after the start is set to NO_SHOW and its
slot released). The sweep runs every minute in the background (see main.py) and can be triggered by staff.
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import auth, notifications
from backend.auth import ADMIN, ATTENDANT, DRIVER, User, require_roles
from backend.db import get_db
from backend.lots import MAINTENANCE, Slot
from backend.reservations import (ALLOC_LOCK, CANCELLED, CONFIRMED, NO_SHOW, NO_SHOW_MINUTES, Reservation,
                                  ReservationOut, Vehicle, _to_utc_naive, advance_for, allocate_slot, blocking,
                                  check_window, get_reservation_or_404, reservation_out, reserved_slot_ids)

FULL_REFUND_HOURS = 1


class ModifyIn(BaseModel):
    start: dt.datetime
    end: dt.datetime

    _times = field_validator("start", "end")(classmethod(lambda cls, v: _to_utc_naive(v)))


def _own_active(db: Session, code: str, user: User) -> Reservation:
    r = get_reservation_or_404(db, code)
    if r.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    if r.status != CONFIRMED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Reservation is {r.status}")
    if auth.utcnow() >= r.start:
        raise HTTPException(status.HTTP_409_CONFLICT, "The reservation has already started")
    return r


def refund_for(r: Reservation, now: dt.datetime) -> float:
    """F-032 refund policy."""
    if r.start - now >= dt.timedelta(hours=FULL_REFUND_HOURS):
        return r.advance_amount
    return round(r.advance_amount * 0.5, 2)


def release_no_shows(db: Session, now: dt.datetime | None = None) -> int:
    """F-033: CONFIRMED reservations not checked in by start + 15 min become NO_SHOW."""
    now = now or auth.utcnow()
    expired = db.scalars(select(Reservation).where(
        Reservation.status == CONFIRMED,
        Reservation.start < now - dt.timedelta(minutes=NO_SHOW_MINUTES))).all()
    for r in expired:
        r.status = NO_SHOW
    db.commit()
    return len(expired)


router = APIRouter(prefix="/api/v1/reservations", tags=["reservations"])
driver_only = require_roles(DRIVER)


@router.post("/release-no-shows", dependencies=[Depends(require_roles(ADMIN, ATTENDANT))])
def run_no_show_sweep(db: Session = Depends(get_db)):
    """Staff can trigger the no-show sweep by hand (it also runs every minute)."""
    return {"released": release_no_shows(db)}


@router.patch("/{code}", response_model=ReservationOut)
def modify(code: str, data: ModifyIn, db: Session = Depends(get_db), user: User = Depends(driver_only)):
    """F-032: move a reservation to a new time window (same rules as a new booking)."""
    r = _own_active(db, code, user)
    now = auth.utcnow()
    check_window(data.start, data.end, now)
    with ALLOC_LOCK:
        clash = db.scalar(select(Reservation.id).where(
            Reservation.vehicle_id == r.vehicle_id, Reservation.id != r.id, blocking(now),
            Reservation.start < data.end, Reservation.end > data.start))
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, "This vehicle already has a reservation in that time")
        slot = db.get(Slot, r.slot_id)
        taken = reserved_slot_ids(db, r.lot_id, data.start, data.end, now, ignore_id=r.id)
        if slot is None or slot.status == MAINTENANCE or slot.id in taken:  # keep the slot if we can
            vehicle = db.get(Vehicle, r.vehicle_id)
            slot = allocate_slot(db, r.lot_id, r.vehicle_type, data.start, data.end, vehicle.needs_accessible,
                                 now, ignore_reservation_id=r.id)
            if slot is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "No suitable slot is free for that time")
        r.slot_id, r.start, r.end = slot.id, data.start, data.end
        r.advance_amount = advance_for(db, r.lot_id, r.vehicle_type, data.start, data.end)  # difference settled (mock)
        r.reminder_sent = False
        notifications.send(db, user.email, f"SPMS reservation {r.code} changed",
                           f"Hi {user.name},\n\nYour reservation {r.code} now runs "
                           f"{r.start:%d %b %Y %H:%M}-{r.end:%H:%M} UTC.\n\nSPMS", "MODIFIED")
        db.commit()
    return reservation_out(db, r)


@router.post("/{code}/cancel", response_model=ReservationOut)
def cancel(code: str, db: Session = Depends(get_db), user: User = Depends(driver_only)):
    """F-032: cancel before the start. Refund 100% if >= 1 h before start, else 50%. Slot freed at once."""
    r = _own_active(db, code, user)
    now = auth.utcnow()
    r.status, r.cancelled_at, r.refund_amount = CANCELLED, now, refund_for(r, now)
    notifications.reservation_cancelled(db, user, r)
    db.commit()
    return reservation_out(db, r)
