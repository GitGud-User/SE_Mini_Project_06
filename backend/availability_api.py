"""Live availability API and WebSocket.

SRS: SPMS-F-020 (free slots per type, per lot) and F-021 (live updates within a few seconds).
Uses the counting functions from backend/availability.py.
"""
import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.availability import count_free_slots, occupancy_percent
from backend.auth import utcnow
from backend.db import SessionLocal, get_db
from backend.lots import SLOT_TYPES, Level, Slot, get_lot_or_404

PUSH_INTERVAL_SECONDS = 2


def build_snapshot(db: Session, lot_id: int) -> dict:
    """Free-slot counts per type and per level, plus occupancy, for one lot."""
    lot = get_lot_or_404(db, lot_id)
    slots = db.scalars(select(Slot).where(Slot.lot_id == lot_id)).all()
    as_dicts = [{"code": s.code, "type": s.slot_type, "status": s.status, "level_id": s.level_id} for s in slots]
    free = count_free_slots(as_dicts)
    levels = db.scalars(select(Level).where(Level.lot_id == lot_id).order_by(Level.floor_no)).all()
    per_level = []
    for level in levels:
        on_level = [s for s in as_dicts if s["level_id"] == level.id]
        per_level.append({"level": level.name, "free": sum(1 for s in on_level if s["status"] == "FREE"),
                          "total": len(on_level)})
    return {
        "lot_id": lot.id,
        "lot_name": lot.name,
        "free_by_type": {t: free.get(t, 0) for t in SLOT_TYPES},
        "total_free": sum(free.values()),
        "total_slots": len(as_dicts),
        "occupancy_percent": occupancy_percent(as_dicts),
        "levels": per_level,
        "updated_at": utcnow().isoformat(timespec="seconds") + "Z",
    }


router = APIRouter(tags=["availability"])


@router.get("/api/v1/lots/{lot_id}/availability")
def get_availability(lot_id: int, db: Session = Depends(get_db)):
    """SPMS-F-020: public view of free slots by vehicle type."""
    return build_snapshot(db, lot_id)


@router.websocket("/ws/lots/{lot_id}/availability")
async def availability_ws(websocket: WebSocket, lot_id: int):
    """SPMS-F-021: push a fresh snapshot every 2 seconds (the UI falls back to polling if this fails)."""
    await websocket.accept()
    try:
        while True:
            with SessionLocal() as db:
                try:
                    snapshot = build_snapshot(db, lot_id)
                except Exception:  # unknown lot
                    await websocket.send_json({"error": "Parking lot not found"})
                    await websocket.close(code=1008)
                    return
            await websocket.send_json(snapshot)
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
