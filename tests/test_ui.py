"""Smoke tests for the basic web UI (issue: Basic web UI)."""
import pytest


def test_root_redirects_to_ui(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"] == "/ui/"


@pytest.mark.parametrize("page, text", [
    ("/ui/", "Live parking availability"),
    ("/ui/login.html", "Log in"),
    ("/ui/gate.html", "Gate console"),
    ("/ui/admin.html", "Admin: lots, levels and slots"),
])
def test_pages_are_served(client, page, text):
    response = client.get(page)
    assert response.status_code == 200 and text in response.text


def test_assets_are_served(client):
    assert client.get("/ui/app.js").status_code == 200
    assert "text/css" in client.get("/ui/style.css").headers["content-type"]
