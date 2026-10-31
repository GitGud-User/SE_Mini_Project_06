"""Tests for SPMS-NF-003: daily backup and a restore drill."""
import pytest

from backend import backup


@pytest.fixture
def backup_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SPMS_BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


def test_backup_and_restore_drill(client, admin_headers, backup_folder):
    client.post("/api/v1/lots", json={"name": "Keep Me"}, headers=admin_headers)
    saved = backup.backup_now()
    assert saved.exists() and saved.parent == backup_folder
    client.post("/api/v1/lots", json={"name": "Added Later"}, headers=admin_headers)
    backup.restore(saved)
    names = [lot["name"] for lot in client.get("/api/v1/lots").json()]
    assert names == ["Keep Me"]


def test_only_newest_7_backups_kept(backup_folder):
    for _ in range(9):
        backup.backup_now()
    assert len(backup.list_backups()) == 7


def test_admin_backup_api(client, admin_headers, attendant_headers, backup_folder):
    assert client.post("/api/v1/admin/backups", headers=attendant_headers).status_code == 403
    made = client.post("/api/v1/admin/backups", headers=admin_headers)
    assert made.status_code == 201 and made.json()["file"].startswith("spms-")
    assert len(client.get("/api/v1/admin/backups", headers=admin_headers).json()) == 1
