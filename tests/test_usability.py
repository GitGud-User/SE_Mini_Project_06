"""SPMS-NF-004 checks that can be automated: every page is responsive (viewport meta), has a language and
a title, every form field has a label, and text colours meet WCAG 2.1 AA contrast (>= 4.5:1).
The 5-user usability test and Lighthouse (>= 90) are done by hand - see the Sprint 2 guide."""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "backend" / "static"
PAGES = sorted(p.name for p in STATIC.glob("*.html"))


class Fields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields, self.labels, self.in_label, self.wrapped = [], set(), 0, set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.in_label += 1
            if a.get("for"):
                self.labels.add(a["for"])
        if tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.fields.append(a.get("id"))
            if self.in_label:
                self.wrapped.add(a.get("id"))

    def handle_endtag(self, tag):
        if tag == "label":
            self.in_label -= 1


@pytest.mark.parametrize("page", PAGES)
def test_page_basics(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    assert '<html lang="en">' in html and "<title>" in html and "<main>" in html


@pytest.mark.parametrize("page", PAGES)
def test_every_field_has_a_label(page):
    p = Fields()
    p.feed((STATIC / page).read_text(encoding="utf-8"))
    unlabeled = [f for f in p.fields if f not in p.labels and f not in p.wrapped]
    assert unlabeled == []


def _luminance(hex_colour):
    channels = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_colour_contrast_wcag_aa():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    var = dict(re.findall(r"--([a-z]+):\s*(#[0-9a-fA-F]{6})", css))
    pairs = [(var["text"], var["bg"]), (var["muted"], var["card"]), (var["muted"], var["bg"]),
             ("#ffffff", var["brand"]), ("#ffffff", "#475569"), ("#ffffff", var["bad"]),
             (var["ok"], "#dcfce7"), (var["bad"], "#fee2e2"), (var["warn"], "#fef3c7"),
             ("#334155", "#e2e8f0"), ("#1e3a8a", "#dbeafe"), ("#cbd5e1", "#0f172a")]
    for fg, bg in pairs:
        assert contrast(fg, bg) >= 4.5, (fg, bg)


def test_driver_can_reserve_in_few_screens(client):
    login = client.get("/ui/login.html").text
    assert 'DRIVER: "reserve.html"' in login  # 1: register/log in -> 2: reserve page
    page = client.get("/ui/reserve.html").text
    for step in ('id="vehicleForm"', 'id="reserveForm"', 'id="done"'):  # vehicle, booking, QR - one page
        assert step in page
