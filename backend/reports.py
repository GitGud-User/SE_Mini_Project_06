"""Admin reports and driver history.

SRS: SPMS-F-070 (dashboard: occupancy %, revenue, average stay and peak hours per lot for a date range),
SPMS-F-071 (the same report as CSV; totals match the dashboard) and SPMS-F-073 (a driver sees only their
own reservations, parking sessions and receipts). Also serves the reservation QR code as an SVG image.

Revenue for a period = exit payments (cash/UPI/card) + reservation advances taken - refunds given.
Occupancy % = parked slot-minutes inside the period / (slots x minutes in the period).
"""
import csv
import datetime as dt
import io
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend import auth
from backend.auth import ADMIN, DRIVER, User, require_roles
from backend.db import get_db
from backend.gate import ParkingSession
from backend.lots import ParkingLot, Slot
from backend.payments import CARD, CASH, PAID, UPI, Payment, Receipt, ReceiptOut, receipt_out
from backend.reservations import (Reservation, ReservationOut, Vehicle, VehicleOut, get_reservation_or_404,
                                  reservation_out)


class LotReport(BaseModel):
    lot_id: int
    lot_name: str
    slots: int
    entries: int
    occupancy_percent: float
    exit_payments: float
    advances: float
    refunds: float
    revenue: float
    average_stay_minutes: float
    peak_hours: list[int]
    entries_by_hour: list[int]


class Summary(BaseModel):
    date_from: dt.date
    date_to: dt.date
    lots: list[LotReport]
    totals: dict


def _range(date_from: dt.date | None, date_to: dt.date | None) -> tuple[dt.date, dt.date]:
    today = auth.utcnow().date()
    date_to = date_to or today
    date_from = date_from or date_to - dt.timedelta(days=6)
    if date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    if (date_to - date_from).days > 366:
        raise HTTPException(422, "Choose a range of at most one year")
    return date_from, date_to


def lot_report(db: Session, lot: ParkingLot, start: dt.datetime, end: dt.datetime) -> LotReport:
    now = auth.utcnow()
    slots = db.scalar(select(func.count()).select_from(Slot).where(Slot.lot_id == lot.id)) or 0
    sessions = db.scalars(select(ParkingSession).where(
        ParkingSession.lot_id == lot.id, ParkingSession.entry_time < end,
        (ParkingSession.exit_time.is_(None)) | (ParkingSession.exit_time > start))).all()
    occupied_minutes = sum(
        max(0.0, (min(s.exit_time or now, end) - max(s.entry_time, start)).total_seconds() / 60) for s in sessions)
    elapsed_end = min(end, max(now, start))  # today's report only counts the minutes that have passed
    period_minutes = max(1.0, (elapsed_end - start).total_seconds() / 60)
    entries = [s for s in sessions if start <= s.entry_time < end]
    by_hour = Counter(s.entry_time.hour for s in entries)
    hourly = [by_hour.get(h, 0) for h in range(24)]
    peaks = [h for h, n in sorted(by_hour.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]

    tickets = select(ParkingSession.ticket).where(ParkingSession.lot_id == lot.id)
    exit_payments = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0.0)).where(
        Payment.ticket.in_(tickets), Payment.status == PAID, Payment.method.in_([CASH, UPI, CARD]),
        Payment.created_at >= start, Payment.created_at < end)) or 0.0
    advances = db.scalar(select(func.coalesce(func.sum(Reservation.advance_amount), 0.0)).where(
        Reservation.lot_id == lot.id, Reservation.created_at >= start, Reservation.created_at < end)) or 0.0
    refunds = db.scalar(select(func.coalesce(func.sum(Reservation.refund_amount), 0.0)).where(
        Reservation.lot_id == lot.id, Reservation.cancelled_at >= start, Reservation.cancelled_at < end)) or 0.0
    avg_stay = db.scalar(select(func.avg(Receipt.minutes)).where(
        Receipt.lot_id == lot.id, Receipt.exit_time >= start, Receipt.exit_time < end)) or 0.0
    occupancy = 100 * occupied_minutes / (slots * period_minutes) if slots else 0.0
    return LotReport(lot_id=lot.id, lot_name=lot.name, slots=slots, entries=len(entries),
                     occupancy_percent=round(occupancy, 1), exit_payments=round(exit_payments, 2),
                     advances=round(advances, 2), refunds=round(refunds, 2),
                     revenue=round(exit_payments + advances - refunds, 2), average_stay_minutes=round(avg_stay, 1),
                     peak_hours=peaks, entries_by_hour=hourly)


def build_summary(db: Session, lot_id: int | None, date_from: dt.date | None, date_to: dt.date | None) -> Summary:
    date_from, date_to = _range(date_from, date_to)
    start = dt.datetime.combine(date_from, dt.time())
    end = dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time())
    query = select(ParkingLot).order_by(ParkingLot.id)
    if lot_id is not None:
        query = query.where(ParkingLot.id == lot_id)
    rows = [lot_report(db, lot, start, end) for lot in db.scalars(query)]
    if lot_id is not None and not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parking lot not found")
    totals = {
        "entries": sum(r.entries for r in rows),
        "revenue": round(sum(r.revenue for r in rows), 2),
        "exit_payments": round(sum(r.exit_payments for r in rows), 2),
        "advances": round(sum(r.advances for r in rows), 2),
        "refunds": round(sum(r.refunds for r in rows), 2),
        "occupancy_percent": round(sum(r.occupancy_percent * r.slots for r in rows) / max(1, sum(r.slots for r in rows)), 1),
    }
    return Summary(date_from=date_from, date_to=date_to, lots=rows, totals=totals)


router = APIRouter(prefix="/api/v1", tags=["reports"])
admin_only = require_roles(ADMIN)


@router.get("/reports/summary", response_model=Summary, dependencies=[Depends(admin_only)])
def summary(lot_id: int | None = None, date_from: dt.date | None = Query(None), date_to: dt.date | None = Query(None),
            db: Session = Depends(get_db)):
    """F-070: occupancy, revenue, average stay and peak hours per lot (default: last 7 days)."""
    return build_summary(db, lot_id, date_from, date_to)


@router.get("/reports/export.csv", dependencies=[Depends(admin_only)])
def export_csv(lot_id: int | None = None, date_from: dt.date | None = Query(None),
               date_to: dt.date | None = Query(None), db: Session = Depends(get_db)):
    """F-071: the dashboard report as a CSV file (opens in Excel / Sheets)."""
    report = build_summary(db, lot_id, date_from, date_to)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["lot_id", "lot", "slots", "entries", "occupancy_percent", "exit_payments", "advances", "refunds",
                "revenue", "average_stay_minutes", "peak_hours"])
    for r in report.lots:
        w.writerow([r.lot_id, r.lot_name, r.slots, r.entries, r.occupancy_percent, f"{r.exit_payments:.2f}",
                    f"{r.advances:.2f}", f"{r.refunds:.2f}", f"{r.revenue:.2f}", r.average_stay_minutes,
                    " ".join(f"{h:02d}:00" for h in r.peak_hours)])
    t = report.totals
    w.writerow(["", "TOTAL", sum(r.slots for r in report.lots), t["entries"], t["occupancy_percent"],
                f"{t['exit_payments']:.2f}", f"{t['advances']:.2f}", f"{t['refunds']:.2f}", f"{t['revenue']:.2f}", "", ""])
    name = f"spms-report-{report.date_from}-to-{report.date_to}.csv"
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class SessionItem(BaseModel):
    ticket: str
    plate: str
    lot_id: int
    entry_time: dt.datetime
    exit_time: dt.datetime | None
    fee: float | None
    status: str
    receipt_no: str | None


class History(BaseModel):
    vehicles: list[VehicleOut]
    reservations: list[ReservationOut]
    sessions: list[SessionItem]
    receipts: list[ReceiptOut]


@router.get("/me/history", response_model=History)
def my_history(db: Session = Depends(get_db), user: User = Depends(require_roles(DRIVER))):
    """F-073: the logged-in driver's vehicles, reservations, parking sessions and receipts - nobody else's."""
    vehicles = db.scalars(select(Vehicle).where(Vehicle.user_id == user.id).order_by(Vehicle.id)).all()
    plates = [v.plate for v in vehicles]
    reservations = db.scalars(select(Reservation).where(Reservation.user_id == user.id)
                              .order_by(Reservation.start.desc())).all()
    sessions = db.scalars(select(ParkingSession).where(ParkingSession.plate.in_(plates))
                          .order_by(ParkingSession.entry_time.desc())).all() if plates else []
    receipts = db.scalars(select(Receipt).where(Receipt.plate.in_(plates))
                          .order_by(Receipt.exit_time.desc())).all() if plates else []
    by_ticket = {r.ticket: r.receipt_no for r in receipts}
    return History(
        vehicles=[VehicleOut(id=v.id, plate=v.plate, vehicle_type=v.vehicle_type, needs_accessible=v.needs_accessible)
                  for v in vehicles],
        reservations=[reservation_out(db, r) for r in reservations],
        sessions=[SessionItem(ticket=s.ticket, plate=s.plate, lot_id=s.lot_id, entry_time=s.entry_time,
                              exit_time=s.exit_time, fee=s.fee, status=s.status, receipt_no=by_ticket.get(s.ticket))
                  for s in sessions],
        receipts=[receipt_out(r) for r in receipts])


@router.get("/reservations/{code}/qr.svg")
def reservation_qr(code: str, db: Session = Depends(get_db), user: User = Depends(require_roles(DRIVER))):
    """The reservation's QR code as an image, to show at the entry gate."""
    r = get_reservation_or_404(db, code)
    if r.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reservation not found")
    token = reservation_out(db, r).qr_token
    if token is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Reservation is {r.status}; no QR code")
    import segno  # imported here so the rest of the app runs even if segno is not installed yet
    buf = io.BytesIO()
    segno.make(token, error="m").save(buf, kind="svg", scale=4, border=2, xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml")
