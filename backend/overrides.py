"""Attendant allocation override (SPMS-F-041).

An attendant can move a parked vehicle to another free, compatible slot (e.g. the allocated slot is
blocked). A reason is required and the change is written to the audit log (SR-007).
"""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import audit, gate
from backend.auth import ADMIN, ATTENDANT, User, require_roles
from backend.db import get_db
from backend.lots import FREE, OCCUPIED, Slot
from backend.reservations import ALLOC_LOCK, WALK_IN_BUFFER_MINUTES, compatible_types, reserved_slot_ids


class ReassignIn(BaseModel):
    slot_code: str = Field(pattern=r"^[A-Za-z0-9]+-\d+$")
    reason: str = Field(min_length=5, max_length=300)

    _text = field_validator("reason")(classmethod(lambda cls, v: audit.no_html(v)))


router = APIRouter(prefix="/api/v1/gate", tags=["gate"])


@router.post("/sessions/{ticket}/reassign", response_model=gate.SessionOut)
def reassign(ticket: str, data: ReassignIn, db: Session = Depends(get_db),
             user: User = Depends(require_roles(ADMIN, ATTENDANT))):
    """F-041: override the automatic allocation with another free compatible slot (audited)."""
    with ALLOC_LOCK:
        session = db.scalar(select(gate.ParkingSession).where(gate.ParkingSession.ticket == ticket,
                                                              gate.ParkingSession.status == gate.OPEN))
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No open parking session with this ticket")
        target = db.scalar(select(Slot).where(Slot.lot_id == session.lot_id, Slot.code == data.slot_code.upper()))
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Slot {data.slot_code} not found in this lot")
        if target.id == session.slot_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "The vehicle is already in this slot")
        if target.status != FREE:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Slot {target.code} is {target.status}")
        if target.slot_type not in compatible_types(session.vehicle_type):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Slot {target.code} does not fit a {session.vehicle_type}")
        now = gate._now()
        if target.id in reserved_slot_ids(db, session.lot_id, now, now + dt.timedelta(minutes=WALK_IN_BUFFER_MINUTES), now):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Slot {target.code} is reserved soon")
        old = db.get(Slot, session.slot_id)
        old.status, target.status, session.slot_id = FREE, OCCUPIED, target.id
        audit.record(db, user, "ALLOCATION_OVERRIDE", "SESSION", ticket, {"slot": old.code},
                     {"slot": target.code}, reason=data.reason)
        db.commit()
    return gate._session_out(db, session)
