"""Payments, exit fee settlement, waivers and receipts.

SRS: SPMS-F-061 (online payment through the gateway's hosted checkout - a mock gateway in test mode -
and cash recorded by the attendant), SPMS-F-062 (digital receipt, viewable and downloadable as PDF),
SPMS-F-063 (a session with an unpaid fee cannot be closed unless an admin records a waiver with a reason).
The reservation advance (F-030) is deducted from the fee at exit.
"""
import datetime as dt
import hashlib
import hmac
import math
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Float, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend import audit, auth, gate
from backend.auth import ADMIN, ATTENDANT, DRIVER, User, get_current_user, require_roles
from backend.billing import compute_fee
from backend.db import Base, get_db
from backend.gate import OPEN, ParkingSession, _clean_plate
from backend.lots import get_tariff
from backend.ratelimit import rate_limit
from backend.reservations import Reservation, Vehicle

CASH, UPI, CARD, WAIVER = "CASH", "UPI", "CARD", "WAIVER"
PAID, PENDING, WAIVED = "PAID", "PENDING", "WAIVED"
EXIT_LEEWAY_MINUTES = 15  # after paying, the driver has 15 minutes to leave without extra charge


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket: Mapped[str] = mapped_column(String(20), index=True)
    amount: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default=PENDING)
    order_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    gateway_payment_id: Mapped[str] = mapped_column(String(40), default="")
    reason: Mapped[str] = mapped_column(String(300), default="")
    recorded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, index=True)


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(20), default="", index=True)
    ticket: Mapped[str] = mapped_column(String(20), unique=True)
    plate: Mapped[str] = mapped_column(String(15), index=True)
    lot_id: Mapped[int] = mapped_column(Integer)
    entry_time: Mapped[dt.datetime] = mapped_column(DateTime)
    exit_time: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    minutes: Mapped[int] = mapped_column(Integer)
    total_fee: Mapped[float] = mapped_column(Float)
    advance_deducted: Mapped[float] = mapped_column(Float, default=0.0)
    amount_paid: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(20))


# ---------- fee calculation ----------
class Due(BaseModel):
    ticket: str
    plate: str
    minutes: int
    total_fee: float
    advance: float
    paid: float
    waived: bool
    amount_due: float


def _minutes(session: ParkingSession, at: dt.datetime) -> int:
    return max(0, math.ceil((at - session.entry_time).total_seconds() / 60))


def paid_total(db: Session, ticket: str) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
        Payment.ticket == ticket, Payment.status == PAID)))


def is_waived(db: Session, ticket: str) -> bool:
    return db.scalar(select(Payment.id).where(Payment.ticket == ticket, Payment.status == WAIVED)) is not None


def advance_for_ticket(db: Session, ticket: str) -> float:
    r = db.scalar(select(Reservation).where(Reservation.session_ticket == ticket))
    return r.advance_amount if r else 0.0


def due_for(db: Session, session: ParkingSession, now: dt.datetime | None = None) -> Due:
    """Fee for the stay so far. Within 15 min of the last payment the fee is frozen at that payment time."""
    now = now or gate._now()
    last_paid = db.scalar(select(func.max(Payment.created_at)).where(Payment.ticket == session.ticket,
                                                                     Payment.status == PAID))
    charge_at = last_paid if last_paid and now - last_paid <= dt.timedelta(minutes=EXIT_LEEWAY_MINUTES) else now
    t = get_tariff(db, session.lot_id, session.vehicle_type)
    minutes = _minutes(session, charge_at)
    total = compute_fee(minutes, t.hourly_rate, t.grace_minutes, t.daily_cap)
    advance = min(advance_for_ticket(db, session.ticket), total)
    paid = paid_total(db, session.ticket)
    return Due(ticket=session.ticket, plate=session.plate, minutes=_minutes(session, now), total_fee=total,
               advance=advance, paid=paid, waived=is_waived(db, session.ticket),
               amount_due=round(max(total - advance - paid, 0.0), 2))


def settle_exit(db: Session, session: ParkingSession, now: dt.datetime) -> "Receipt":
    """Called by the gate at exit. Raises 402 if money is still owed (F-063); otherwise issues the receipt."""
    due = due_for(db, session, now)
    if due.amount_due > 0 and not due.waived:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"Payment of Rs {due.amount_due:.2f} required before exit (ticket {session.ticket})")
    methods = sorted({p.method for p in db.scalars(select(Payment).where(
        Payment.ticket == session.ticket, Payment.status.in_([PAID, WAIVED])))})
    if due.advance:
        methods.insert(0, "ADVANCE")
    receipt = Receipt(ticket=session.ticket, plate=session.plate, lot_id=session.lot_id,
                      entry_time=session.entry_time, exit_time=now, minutes=due.minutes, total_fee=due.total_fee,
                      advance_deducted=due.advance, amount_paid=due.paid, method="+".join(methods) or "FREE")
    db.add(receipt)
    db.flush()
    receipt.receipt_no = f"RCPT-{receipt.id:06d}"
    session.fee = due.total_fee
    return receipt


# ---------- mock payment gateway (test mode, Razorpay-style signature) ----------
def _sign(order_id: str, payment_id: str) -> str:
    return hmac.new(auth.SECRET_KEY.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _confirm(db: Session, order_id: str, payment_id: str, signature: str, method: str | None = None) -> Payment:
    p = db.scalar(select(Payment).where(Payment.order_id == order_id))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown order")
    if not hmac.compare_digest(_sign(order_id, payment_id), signature):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid payment signature")
    if p.status != PAID:
        p.status, p.gateway_payment_id, p.created_at = PAID, payment_id, gate._now()
        if method in (UPI, CARD):
            p.method = method
        db.commit()
    return p


# ---------- receipt PDF (no extra library needed) ----------
def _pdf(lines: list[str]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text = ["BT", "/F1 18 Tf", "60 780 Td", f"({esc(lines[0])}) Tj", "/F1 11 Tf"]
    for line in lines[1:]:
        text += ["0 -22 Td", f"({esc(line)}) Tj"]
    stream = "\n".join(text + ["ET"]).encode("latin-1", "replace")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------- schemas ----------
class TicketIn(BaseModel):
    ticket: str = Field(pattern=r"^T-\d{6}$")


class CashIn(TicketIn):
    amount: float = Field(gt=0)


class WaiverIn(TicketIn):
    reason: str = Field(min_length=5, max_length=300)

    _text = field_validator("reason")(classmethod(lambda cls, v: audit.no_html(v)))


class VerifyIn(BaseModel):
    order_id: str
    payment_id: str
    signature: str


class PaymentOut(BaseModel):
    id: int
    ticket: str
    amount: float
    method: str
    status: str
    order_id: str


class ReceiptOut(BaseModel):
    receipt_no: str
    ticket: str
    plate: str
    entry_time: dt.datetime
    exit_time: dt.datetime
    minutes: int
    total_fee: float
    advance_deducted: float
    amount_paid: float
    method: str


def _p_out(p: Payment) -> PaymentOut:
    return PaymentOut(id=p.id, ticket=p.ticket, amount=p.amount, method=p.method, status=p.status, order_id=p.order_id)


def receipt_out(r: Receipt) -> ReceiptOut:
    return ReceiptOut(receipt_no=r.receipt_no, ticket=r.ticket, plate=r.plate, entry_time=r.entry_time,
                      exit_time=r.exit_time, minutes=r.minutes, total_fee=r.total_fee,
                      advance_deducted=r.advance_deducted, amount_paid=r.amount_paid, method=r.method)


def _open_session(db: Session, ticket: str) -> ParkingSession:
    s = db.scalar(select(ParkingSession).where(ParkingSession.ticket == ticket, ParkingSession.status == OPEN))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open parking session with this ticket")
    return s


# ---------- routes ----------
router = APIRouter(prefix="/api/v1", tags=["payments"])
staff = require_roles(ADMIN, ATTENDANT)


@router.get("/payments/due", response_model=Due, dependencies=[Depends(staff)])
def amount_due(plate: str, db: Session = Depends(get_db)):
    """What the vehicle owes right now (shown at the exit gate)."""
    s = db.scalar(select(ParkingSession).where(ParkingSession.plate == _clean_plate(plate),
                                               ParkingSession.status == OPEN))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open parking session for this vehicle")
    return due_for(db, s)


@router.post("/payments/cash", response_model=PaymentOut, status_code=201,
             dependencies=[Depends(rate_limit("payment", 30))])
def pay_cash(data: CashIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    """F-061: attendant records cash received for the amount due."""
    due = due_for(db, _open_session(db, data.ticket))
    if due.amount_due <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to pay for this ticket")
    if round(data.amount, 2) < due.amount_due:
        raise HTTPException(422, f"Amount due is Rs {due.amount_due:.2f}")
    p = Payment(ticket=data.ticket, amount=due.amount_due, method=CASH, status=PAID, recorded_by=user.id,
                created_at=gate._now())
    db.add(p)
    db.commit()
    return _p_out(p)


@router.post("/payments/online/checkout", status_code=201, dependencies=[Depends(rate_limit("payment", 30))])
def online_checkout(data: TicketIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    """F-061: start a UPI/card payment; returns the hosted checkout URL of the (mock) gateway."""
    due = due_for(db, _open_session(db, data.ticket))
    if due.amount_due <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to pay for this ticket")
    order_id = "order_" + secrets.token_hex(8)
    db.add(Payment(ticket=data.ticket, amount=due.amount_due, method=UPI, status=PENDING, order_id=order_id,
                   recorded_by=user.id, created_at=gate._now()))
    db.commit()
    return {"order_id": order_id, "amount": due.amount_due, "checkout_url": f"/api/v1/payments/gateway/{order_id}"}


@router.post("/payments/online/verify", response_model=PaymentOut,
             dependencies=[Depends(rate_limit("payment-verify", 30))])
def online_verify(data: VerifyIn, db: Session = Depends(get_db)):
    """Gateway callback: the signature proves the payment really happened (forged ones get 400)."""
    return _p_out(_confirm(db, data.order_id, data.payment_id, data.signature))


@router.get("/payments/gateway/{order_id}", response_class=HTMLResponse, include_in_schema=False)
def gateway_page(order_id: str, db: Session = Depends(get_db)):
    """Mock hosted checkout (TEST MODE): stands in for the real UPI/card page."""
    p = db.scalar(select(Payment).where(Payment.order_id == order_id))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown order")
    state = "Already paid" if p.status == PAID else f"Pay Rs {p.amount:.2f} for ticket {p.ticket}"
    return f"""<!doctype html><html><head><meta name=viewport content="width=device-width, initial-scale=1">
<title>Mock payment gateway</title><link rel="stylesheet" href="/ui/style.css"></head><body><main>
<div class="card"><h2>Mock payment gateway <span class="pill RESERVED">TEST MODE</span></h2>
<p>{state}</p>
<form method="post" action="/api/v1/payments/gateway/{order_id}/pay?method=UPI"><button>Pay with UPI</button></form>
<form method="post" action="/api/v1/payments/gateway/{order_id}/pay?method=CARD"><button class="secondary">Pay with card</button></form>
<p class="muted">No real money moves. Close this tab and click "Log exit" again after paying.</p></div></main></body></html>"""


@router.post("/payments/gateway/{order_id}/pay", response_class=HTMLResponse, include_in_schema=False)
def gateway_pay(order_id: str, method: str = UPI, db: Session = Depends(get_db)):
    payment_id = "pay_" + secrets.token_hex(8)
    p = _confirm(db, order_id, payment_id, _sign(order_id, payment_id), method.upper())
    return f"""<!doctype html><html><head><title>Payment successful</title><link rel="stylesheet" href="/ui/style.css">
</head><body><main><div class="card"><h2>Payment successful</h2><p>Rs {p.amount:.2f} paid by {p.method}
for ticket {p.ticket}.</p><p class="muted">You can close this tab.</p></div></main></body></html>"""


@router.post("/payments/waive", response_model=PaymentOut, status_code=201)
def waive(data: WaiverIn, db: Session = Depends(get_db), user: User = Depends(require_roles(ADMIN))):
    """F-063: admin waives the unpaid fee with a reason (written to the audit log)."""
    due = due_for(db, _open_session(db, data.ticket))
    p = Payment(ticket=data.ticket, amount=due.amount_due, method=WAIVER, status=WAIVED, reason=data.reason,
                recorded_by=user.id, created_at=gate._now())
    db.add(p)
    audit.record(db, user, "FEE_WAIVER", "SESSION", data.ticket, {"amount_due": due.amount_due},
                 {"waived": due.amount_due}, reason=data.reason)
    db.commit()
    return _p_out(p)


def _receipt_or_404(db: Session, receipt_no: str, user: User) -> Receipt:
    r = db.scalar(select(Receipt).where(Receipt.receipt_no == receipt_no))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    if user.role == DRIVER:  # drivers see receipts of their own registered vehicles only
        owner = db.scalar(select(Vehicle.user_id).where(Vehicle.plate == r.plate))
        if owner != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    return r


@router.get("/receipts/{receipt_no}", response_model=ReceiptOut)
def get_receipt(receipt_no: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """F-062: receipt details (receipt no., plate, entry/exit, duration, amount, method)."""
    return receipt_out(_receipt_or_404(db, receipt_no, user))


@router.get("/receipts/{receipt_no}/pdf")
def get_receipt_pdf(receipt_no: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """F-062: the same receipt as a downloadable PDF."""
    r = _receipt_or_404(db, receipt_no, user)
    lines = [f"SPMS parking receipt {r.receipt_no}",
             f"Ticket: {r.ticket}", f"Vehicle: {r.plate}",
             f"Entry: {r.entry_time:%d-%m-%Y %H:%M} UTC", f"Exit:  {r.exit_time:%d-%m-%Y %H:%M} UTC",
             f"Duration: {r.minutes} min", f"Parking fee: Rs {r.total_fee:.2f}",
             f"Advance deducted: Rs {r.advance_deducted:.2f}", f"Paid at exit: Rs {r.amount_paid:.2f}",
             f"Method: {r.method}", "Thank you for parking with SPMS."]
    return Response(_pdf(lines), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{r.receipt_no}.pdf"'})
