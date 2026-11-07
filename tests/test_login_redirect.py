"""Regression test: logging in as a role that may not open the page in ?next= must not loop back to login."""


def test_login_only_follows_next_for_allowed_pages(client):  # TC-AUTH-03
    login = client.get("/ui/login.html").text
    assert "SPMS.canOpen(data.role, next)" in login


def test_role_page_map_matches_pages(client):  # TC-AUTH-03
    js = client.get("/ui/app.js").text
    assert 'DRIVER: ["index.html", "reserve.html", "history.html"]' in js
    assert "Not available for your account" in js  # wrong role gets a message, not a redirect loop
    for page in ("index.html", "reserve.html", "history.html", "gate.html", "admin.html", "dashboard.html"):
        assert client.get(f"/ui/{page}").status_code == 200
