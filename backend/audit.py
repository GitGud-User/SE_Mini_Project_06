"""Append-only audit log for privileged actions.

SRS: SPMS-SR-007 - privileged actions (tariff changes, slot overrides, fee waivers, ...) are written to an
append-only log with user, role, timestamp, action and before/after values. Rows can be added but never
updated or deleted: the ORM refuses it and the API only offers read endpoints.
Also provides `no_html()`, the shared validator for SPMS-SR-004 (no markup in free-text fields).
"""
import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import DateTime, Integer, String, Text, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend import auth
from backend.auth import ADMIN, User, require_roles
from backend.db import Base, get_db

RETENTION_DAYS = 365  # SR-007: keep audit rows for at least one year (nothing is ever deleted)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_email: Mapped[str] = mapped_column(String(120), default="system")
    role: Mapped[str] = mapped_column(String(20), default="SYSTEM")
    action: Mapped[str] = mapped_column(String(40), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    before: Mapped[str] = mapped_column(Text, default="{}")
    after: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(String(300), default="")


@event.listens_for(AuditLog, "before_update")
def _no_update(mapper, connection, target):
    raise PermissionError("audit log is append-only: rows cannot be updated")


@event.listens_for(AuditLog, "before_delete")
def _no_delete(mapper, connection, target):
    raise PermissionError("audit log is append-only: rows cannot be deleted")


def record(db: Session, user: User | None, action: str, entity: str, entity_id, before=None, after=None,
           reason: str = "") -> AuditLog:
    """Add an audit row. The caller commits, so the change and its audit entry are saved together."""
    row = AuditLog(
        created_at=auth.utcnow(),
        user_id=user.id if user else None,
        user_email=user.email if user else "system",
        role=user.role if user else "SYSTEM",
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        before=json.dumps(before or {}, default=str, sort_keys=True),
        after=json.dumps(after or {}, default=str, sort_keys=True),
        reason=reason,
    )
    db.add(row)
    return row


def no_html(value: str) -> str:
    """SR-004 validator: free text may not contain HTML/script markup."""
    if any(ch in value for ch in "<>"):
        raise ValueError("must not contain < or >")
    if any(ord(ch) < 32 for ch in value):
        raise ValueError("must not contain control characters")
    return value


class AuditOut(BaseModel):
    id: int
    created_at: dt.datetime
    user_email: str
    role: str
    action: str
    entity: str
    entity_id: str
    before: dict
    after: dict
    reason: str


def _out(row: AuditLog) -> AuditOut:
    return AuditOut(id=row.id, created_at=row.created_at, user_email=row.user_email, role=row.role,
                    action=row.action, entity=row.entity, entity_id=row.entity_id,
                    before=json.loads(row.before), after=json.loads(row.after), reason=row.reason)


router = APIRouter(prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(require_roles(ADMIN))])


@router.get("", response_model=list[AuditOut])
def list_audit(action: str | None = None, entity: str | None = None,
               limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Admin: newest audit entries first. There are deliberately no update/delete endpoints."""
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        query = query.where(AuditLog.action == action.upper())
    if entity:
        query = query.where(AuditLog.entity == entity.upper())
    return [_out(r) for r in db.scalars(query)]


@router.get("/{entry_id}", response_model=AuditOut)
def get_audit(entry_id: int, db: Session = Depends(get_db)):
    row = db.get(AuditLog, entry_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit entry not found")
    return _out(row)
