"""Regression tests for the accessibility / interface review fixes (WCAG 2.2 AA, NF-004)."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "backend" / "static"


def _lum(hex_colour):
    c = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def css_vars():
    return dict(re.findall(r"--([a-z]+):\s*(#[0-9a-fA-F]{6})", (STATIC / "style.css").read_text(encoding="utf-8")))


def test_field_borders_and_focus_ring_are_visible():  # WCAG 1.4.11 / 2.4.7
    v = css_vars()
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "border: 1px solid var(--field)" in css and "outline: 3px solid var(--focus)" in css
    assert contrast(v["field"], "#ffffff") >= 3
    for background in ("#ffffff", "#f5f7fa", "#eef2ff"):  # cards, page, page tint
        assert contrast(v["focus"], background) >= 3
    assert "header :focus-visible { outline-color: #fbbf24; }" in css
    for header_colour in ("#0f172a", "#1e1b4b"):  # both ends of the header gradient
        assert contrast("#fbbf24", header_colour) >= 3
        assert contrast("#cbd5e1", header_colour) >= 4.5  # menu link text


def test_inputs_are_16px_so_phones_do_not_zoom():
    assert "font-size: 16px" in (STATIC / "style.css").read_text(encoding="utf-8")


@pytest.mark.parametrize("page", ["login.html", "gate.html", "admin.html", "reserve.html", "history.html", "dashboard.html"])
def test_result_messages_are_announced(page):  # WCAG 4.1.3
    html = (STATIC / page).read_text(encoding="utf-8")
    messages = re.findall(r'<div class="msg"[^>]*>', html)
    assert messages and all('role="status"' in m for m in messages)


def test_login_fields_can_be_autofilled_and_page_has_h1(client):  # WCAG 1.3.5, 1.3.1
    html = client.get("/ui/login.html").text
    for value in ("username", "current-password", "name", "email", "tel-national", "new-password"):
        assert f'autocomplete="{value}"' in html
    assert "<h1>" in html


def test_validation_errors_read_as_instructions(client):
    bad = {"name": "Ravi", "email": "ravi@x.com", "phone": "98765", "password": "abc"}
    messages = [d["msg"] for d in client.post("/api/v1/auth/register", json=bad).json()["detail"]]
    assert any("Enter a 10-digit phone number" in m for m in messages)
    assert any("Choose a password with at least 6 characters" in m for m in messages)
    assert 'replace(/^Value error, /, "")' in client.get("/ui/app.js").text


def test_buttons_say_what_they_do(client):
    gate, history, admin = (client.get(f"/ui/{p}").text for p in ("gate.html", "history.html", "admin.html"))
    assert "Record cash payment" in gate and "Pay by UPI or card" in gate
    assert "Cancel booking" in history and "Show QR code" in history
    assert "Start maintenance" in admin and "Reopen slot" in admin


def test_live_updates_can_be_paused_and_chart_has_text(client):  # WCAG 2.2.2, 1.1.1
    assert 'id="pause"' in client.get("/ui/index.html").text
    dashboard = client.get("/ui/dashboard.html").text
    assert 'class="sr-only"' in dashboard and 'id="barsTable"' in dashboard


def test_skip_link_on_every_page(client):  # WCAG 2.4.1
    assert 'class="skip" href="#main"' in client.get("/ui/app.js").text


def test_payment_gateway_page_has_language_and_heading():  # WCAG 3.1.1, 1.3.1
    src = (Path(__file__).resolve().parent.parent / "backend" / "payments.py").read_text(encoding="utf-8")
    assert src.count('<html lang="en">') == 2 and "<h1>Mock payment gateway" in src and "<h1>Payment successful" in src


def test_motion_respects_reduced_motion():  # WCAG 2.3.3 / user preference
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "transition: all" not in css
    motion = css[css.index("/* motion: transform + opacity only */"):]
    for prop in re.findall(r"transition:\s*([^;]+);", motion):
        assert all(part.strip().split()[0] in ("opacity", "transform") for part in prop.split(","))


@pytest.mark.parametrize("tint", ["#f3f0ff", "#eff6ff", "#ecfdf5", "#ecfeff", "#fffbeb", "#eef2ff"])
def test_tinted_tiles_keep_text_readable(tint):  # WCAG 1.4.3 on the new coloured tiles
    v = css_vars()
    assert contrast(v["text"], tint) >= 4.5 and contrast(v["muted"], tint) >= 4.5


@pytest.mark.parametrize("stop", ["#4338ca", "#7c3aed", "#0e7490"])
def test_white_text_readable_on_page_gradient(stop):  # headings sit directly on the vibrant background
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert f"{stop}" in css and "main > h1, main > p, main > p.muted { color: #ffffff; }" in css
    assert contrast("#ffffff", stop) >= 4.5
