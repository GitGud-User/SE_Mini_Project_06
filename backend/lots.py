"""Parking lots, levels, slots and tariffs (admin configuration).

SRS: SPMS-F-010 (create/manage lots and levels), F-011 (create slots with types),
F-012 (mark slots under maintenance). Tariffs are stored per lot and vehicle type
and are used by the gate module to compute fees.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend.auth import ADMIN, ATTENDANT, require_roles
from backend.db import Base, get_db

TWO_WHEELER, FOUR_WHEELER, EV, ACCESSIBLE = "TWO_WHEELER", "FOUR_WHEELER", "EV", "ACCESSIBLE"
SLOT_TYPES = (TWO_WHEELER, FOUR_WHEELER, EV, ACCESSIBLE)

FREE, OCCUPIED, RESERVED, MAINTENANCE = "FREE", "OCCUPIED", "RESERVED", "MAINTENANCE"

DEFAULT_HOURLY_RATE = 20.0
DEFAULT_GRACE_MINUTES = 15


# ---------- models ----------
class ParkingLot(Base):
    __tablename__ = "parking_lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    address: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Level(Base):
    __tablename__ = "levels"
    __table_args__ = (UniqueConstraint("lot_id", "floor_no"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("parking_lots.id"))
    floor_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("lot_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("parking_lots.id"), index=True)
    level_id: Mapped[int] = mapped_column(ForeignKey("levels.id"))
    code: Mapped[str] = mapped_column(String(20))
    slot_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=FREE)
    distance_to_entry: Mapped[int] = mapped_column(Integer, default=0)


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (UniqueConstraint("lot_id", "vehicle_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("parking_lots.id"))
    vehicle_type: Mapped[str] = mapped_column(String(20))
    hourly_rate: Mapped[float] = mapped_column(Float)
    grace_minutes: Mapped[int] = mapped_column(Integer, default=DEFAULT_GRACE_MINUTES)
    daily_cap: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------- schemas ----------
def _check_type(v: str) -> str:
    v = v.strip().upper()
    if v not in SLOT_TYPES:
        raise ValueError(f"type must be one of {', '.join(SLOT_TYPES)}")
    return v


class LotIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    address: str = ""


class LotPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    address: str | None = None
    is_active: bool | None = None


class LotOut(BaseModel):
    id: int
    name: str
    address: str
    is_active: bool


class LevelIn(BaseModel):
    floor_no: int
    name: str = Field(min_length=1, max_length=50)


class LevelOut(BaseModel):
    id: int
    lot_id: int
    floor_no: int
    name: str


class SlotBulkIn(BaseModel):
    level_id: int
    slot_type: str
    count: int = Field(ge=1, le=500)
    prefix: str = Field(default="S", min_length=1, max_length=5)

    _type = field_validator("slot_type")(classmethod(lambda cls, v: _check_type(v)))


class SlotOut(BaseModel):
    id: int
    lot_id: int
    level_id: int
    code: str
    slot_type: str
    status: str
    distance_to_entry: int


class MaintenanceIn(BaseModel):
    under_maintenance: bool


class TariffIn(BaseModel):
    vehicle_type: str
    hourly_rate: float = Field(gt=0)
    grace_minutes: int = Field(default=DEFAULT_GRACE_MINUTES, ge=0, le=120)
    daily_cap: float | None = Field(default=None, gt=0)

    _type = field_validator("vehicle_type")(classmethod(lambda cls, v: _check_type(v)))


class TariffOut(BaseModel):
    vehicle_type: str
    hourly_rate: float
    grace_minutes: int
    daily_cap: float | None


def _lot_out(lot: ParkingLot) -> LotOut:
    return LotOut(id=lot.id, name=lot.name, address=lot.address, is_active=lot.is_active)


def _slot_out(s: Slot) -> SlotOut:
    return SlotOut(id=s.id, lot_id=s.lot_id, level_id=s.level_id, code=s.code,
                   slot_type=s.slot_type, status=s.status, distance_to_entry=s.distance_to_entry)


# ---------- helpers used by other modules ----------
def get_lot_or_404(db: Session, lot_id: int) -> ParkingLot:
    lot = db.get(ParkingLot, lot_id)
    if lot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parking lot not found")
    return lot


def find_best_free_slot(db: Session, lot_id: int, vehicle_type: str) -> Slot | None:
    """Nearest FREE slot of the requested type (used by gate entry and reservations)."""
    return db.scalar(
        select(Slot)
        .where(Slot.lot_id == lot_id, Slot.slot_type == vehicle_type, Slot.status == FREE)
        .order_by(Slot.distance_to_entry, Slot.id)
        .limit(1)
    )


def get_tariff(db: Session, lot_id: int, vehicle_type: str) -> TariffOut:
    """Tariff for the lot and vehicle type, or a sensible default if none is set."""
    t = db.scalar(select(Tariff).where(Tariff.lot_id == lot_id, Tariff.vehicle_type == vehicle_type))
    if t is None:
        return TariffOut(vehicle_type=vehicle_type, hourly_rate=DEFAULT_HOURLY_RATE,
                         grace_minutes=DEFAULT_GRACE_MINUTES, daily_cap=None)
    return TariffOut(vehicle_type=t.vehicle_type, hourly_rate=t.hourly_rate,
                     grace_minutes=t.grace_minutes, daily_cap=t.daily_cap)


# ---------- routes ----------
router = APIRouter(prefix="/api/v1", tags=["lots"])
admin_only = require_roles(ADMIN)
staff = require_roles(ADMIN, ATTENDANT)


@router.get("/lots", response_model=list[LotOut])
def list_lots(db: Session = Depends(get_db)):
    """Public list of parking lots."""
    return [_lot_out(lot) for lot in db.scalars(select(ParkingLot).order_by(ParkingLot.id))]


@router.post("/lots", response_model=LotOut, status_code=201, dependencies=[Depends(admin_only)])
def create_lot(data: LotIn, db: Session = Depends(get_db)):
    """SPMS-F-010: admin creates a parking lot."""
    if db.scalar(select(ParkingLot).where(ParkingLot.name == data.name.strip())):
        raise HTTPException(status.HTTP_409_CONFLICT, "A lot with this name already exists")
    lot = ParkingLot(name=data.name.strip(), address=data.address.strip())
    db.add(lot)
    db.commit()
    return _lot_out(lot)


@router.patch("/lots/{lot_id}", response_model=LotOut, dependencies=[Depends(admin_only)])
def update_lot(lot_id: int, data: LotPatch, db: Session = Depends(get_db)):
    """SPMS-F-010: admin edits or deactivates a lot."""
    lot = get_lot_or_404(db, lot_id)
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(lot, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    return _lot_out(lot)


@router.post("/lots/{lot_id}/levels", response_model=LevelOut, status_code=201,
             dependencies=[Depends(admin_only)])
def add_level(lot_id: int, data: LevelIn, db: Session = Depends(get_db)):
    """SPMS-F-010: add a level (floor) to a lot."""
    get_lot_or_404(db, lot_id)
    if db.scalar(select(Level).where(Level.lot_id == lot_id, Level.floor_no == data.floor_no)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This floor already exists in the lot")
    level = Level(lot_id=lot_id, floor_no=data.floor_no, name=data.name)
    db.add(level)
    db.commit()
    return LevelOut(id=level.id, lot_id=lot_id, floor_no=level.floor_no, name=level.name)


@router.get("/lots/{lot_id}/levels", response_model=list[LevelOut])
def list_levels(lot_id: int, db: Session = Depends(get_db)):
    get_lot_or_404(db, lot_id)
    levels = db.scalars(select(Level).where(Level.lot_id == lot_id).order_by(Level.floor_no))
    return [LevelOut(id=lv.id, lot_id=lv.lot_id, floor_no=lv.floor_no, name=lv.name) for lv in levels]


@router.post("/lots/{lot_id}/slots", response_model=list[SlotOut], status_code=201,
             dependencies=[Depends(admin_only)])
def add_slots(lot_id: int, data: SlotBulkIn, db: Session = Depends(get_db)):
    """SPMS-F-011: create several slots of one type on a level, e.g. A-1 .. A-20."""
    get_lot_or_404(db, lot_id)
    level = db.get(Level, data.level_id)
    if level is None or level.lot_id != lot_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Level not found in this lot")
    existing = db.scalars(select(Slot).where(Slot.lot_id == lot_id, Slot.code.like(f"{data.prefix}-%"))).all()
    start = len(existing)
    created = []
    for i in range(1, data.count + 1):
        n = start + i
        slot = Slot(lot_id=lot_id, level_id=level.id, code=f"{data.prefix}-{n}", slot_type=data.slot_type,
                    status=FREE, distance_to_entry=level.floor_no * 100 + n)
        db.add(slot)
        created.append(slot)
    db.commit()
    return [_slot_out(s) for s in created]


@router.get("/lots/{lot_id}/slots", response_model=list[SlotOut])
def list_slots(lot_id: int, db: Session = Depends(get_db)):
    """All slots of a lot with their current status."""
    get_lot_or_404(db, lot_id)
    return [_slot_out(s) for s in db.scalars(select(Slot).where(Slot.lot_id == lot_id).order_by(Slot.id))]


@router.patch("/slots/{slot_id}/maintenance", response_model=SlotOut, dependencies=[Depends(staff)])
def set_maintenance(slot_id: int, data: MaintenanceIn, db: Session = Depends(get_db)):
    """SPMS-F-012: put a slot under maintenance (it is then never allocated) or bring it back."""
    slot = db.get(Slot, slot_id)
    if slot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Slot not found")
    if data.under_maintenance:
        if slot.status == OCCUPIED:
            raise HTTPException(status.HTTP_409_CONFLICT, "Slot is occupied; wait until the vehicle exits")
        slot.status = MAINTENANCE
    elif slot.status == MAINTENANCE:
        slot.status = FREE
    db.commit()
    return _slot_out(slot)


@router.put("/lots/{lot_id}/tariffs", response_model=TariffOut, dependencies=[Depends(admin_only)])
def set_tariff(lot_id: int, data: TariffIn, db: Session = Depends(get_db)):
    """Create or update the tariff for one vehicle type in a lot."""
    get_lot_or_404(db, lot_id)
    t = db.scalar(select(Tariff).where(Tariff.lot_id == lot_id, Tariff.vehicle_type == data.vehicle_type))
    if t is None:
        t = Tariff(lot_id=lot_id, vehicle_type=data.vehicle_type)
        db.add(t)
    t.hourly_rate, t.grace_minutes, t.daily_cap = data.hourly_rate, data.grace_minutes, data.daily_cap
    db.commit()
    return get_tariff(db, lot_id, data.vehicle_type)


@router.get("/lots/{lot_id}/tariffs", response_model=list[TariffOut])
def list_tariffs(lot_id: int, db: Session = Depends(get_db)):
    get_lot_or_404(db, lot_id)
    return [get_tariff(db, lot_id, t) for t in SLOT_TYPES]
