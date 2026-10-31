"""Email notifications (SPMS-F-072).

Drivers get an email when a reservation is confirmed, when it is cancelled (with the refund), and
15 minutes before it starts. Every email is stored in the `email_outbox` table (admins can read it at
GET /api/v1/admin/outbox - handy as a test inbox). If SPMS_SMTP_HOST is set the email is also sent
through that SMTP server (e.g. a Mailtrap/MailHog test inbox): SPMS_SMTP_PORT, SPMS_SMTP_USER,
SPMS_SMTP_PASSWORD, SPMS_MAIL_FROM.
"""
import datetime as dt
import os
import smtplib
from email.message import EmailMessage

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend import auth
from backend.auth import ADMIN, User, require_roles
from backend.db import Base, get_db

REMINDER_MINUTES = 15


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    to: Mapped[str] = mapped_column(String(120), index=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)


def send(db: Session, to: str, subject: str, body: str, kind: str) -> EmailOutbox:
    """Store the email and, if SMTP is configured, deliver it. Never raises on mail errors."""
    row = EmailOutbox(to=to, subject=subject, body=body, kind=kind, created_at=auth.utcnow())
    db.add(row)
    host = os.getenv("SPMS_SMTP_HOST")
    if host:
        msg = EmailMessage()
        msg["From"] = os.getenv("SPMS_MAIL_FROM", "SPMS <no-reply@spms.local>")
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(host, int(os.getenv("SPMS_SMTP_PORT", "25")), timeout=10) as smtp:
                if os.getenv("SPMS_SMTP_USER"):
                    smtp.starttls()
                    smtp.login(os.getenv("SPMS_SMTP_USER"), os.getenv("SPMS_SMTP_PASSWORD", ""))
                smtp.send_message(msg)
            row.sent = True
        except (OSError, smtplib.SMTPException):
            row.sent = False  # stays in the outbox; the reservation itself still succeeds
    return row


def _when(r) -> str:
    return f"{r.start:%d %b %Y %H:%M}-{r.end:%H:%M} UTC"


def reservation_confirmed(db: Session, user: User, r) -> None:
    send(db, user.email, f"SPMS reservation {r.code} confirmed",
         f"Hi {user.name},\n\nYour reservation {r.code} for {r.plate} is confirmed for {_when(r)}.\n"
         f"Advance paid: Rs {r.advance_amount:.2f}. Show the QR code at the entry gate within 15 minutes "
         f"of the start time.\n\nSPMS", "CONFIRMED")


def reservation_cancelled(db: Session, user: User, r) -> None:
    send(db, user.email, f"SPMS reservation {r.code} cancelled",
         f"Hi {user.name},\n\nYour reservation {r.code} ({_when(r)}) was cancelled.\n"
         f"Refund: Rs {r.refund_amount or 0:.2f} of the Rs {r.advance_amount:.2f} advance.\n\nSPMS", "CANCELLED")


def send_due_reminders(db: Session, now: dt.datetime | None = None) -> int:
    """Email drivers whose reservation starts within the next 15 minutes (once per reservation)."""
    from backend.reservations import CONFIRMED, Reservation  # reservations imports this module
    now = now or auth.utcnow()
    due = db.scalars(select(Reservation).where(
        Reservation.status == CONFIRMED, Reservation.reminder_sent.is_(False), Reservation.start > now,
        Reservation.start <= now + dt.timedelta(minutes=REMINDER_MINUTES))).all()
    for r in due:
        user = db.get(User, r.user_id)
        send(db, user.email, f"Reminder: SPMS reservation {r.code} starts soon",
             f"Hi {user.name},\n\nYour reservation {r.code} for {r.plate} starts at {r.start:%H:%M} UTC. "
             f"Please arrive within 15 minutes of the start time.\n\nSPMS", "REMINDER")
        r.reminder_sent = True
    db.commit()
    return len(due)


class OutboxOut(BaseModel):
    id: int
    to: str
    subject: str
    body: str
    kind: str
    sent: bool
    created_at: dt.datetime


router = APIRouter(prefix="/api/v1/admin/outbox", tags=["notifications"])


@router.get("", response_model=list[OutboxOut], dependencies=[Depends(require_roles(ADMIN))])
def outbox(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.scalars(select(EmailOutbox).order_by(EmailOutbox.id.desc()).limit(min(limit, 200)))
    return [OutboxOut(id=e.id, to=e.to, subject=e.subject, body=e.body, kind=e.kind, sent=e.sent,
                      created_at=e.created_at) for e in rows]
