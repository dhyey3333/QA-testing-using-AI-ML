"""Company-readiness: encrypted secrets, reset links, two-factor login, the activity log, deleting
a client's or an agency's data, backups that restore, and a health check. Fake data only."""

import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from nightshift.hosted.backup import BackupError, make_backup, restore_backup
from nightshift.hosted.files import ProjectFiles
from nightshift.hosted.jobs import Runner
from nightshift.hosted.server import App, serve
from nightshift.hosted.store import Store
from nightshift.hosted.vault import Vault, VaultError
from nightshift.totp import totp

ADMIN = ("admin@agency.test", "test-admin-password")  # fake, for these tests only
STAFF = ("staff@agency.test", "test-staff-password")
SPEC = "name: smoke\nurl: https://shop.example.test/\nsteps:\n  - open the shop\nexpect:\n  - the shop is shown\n"


@pytest.fixture
def hosted(tmp_path):
    store = Store(tmp_path / "data" / "nightshift.db")
    store.add_user(ADMIN[0], "Admin", ADMIN[1], admin=True)
    store.add_user(STAFF[0], "Staff", STAFF[1])
    runner = Runner(store, tmp_path / "data", max_runs=1, parallel=1, backup_dir=tmp_path / "backups")
    server = serve(App(store, runner), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SimpleNamespace(base=f"http://127.0.0.1:{server.server_port}", store=store, runner=runner,
                          data=tmp_path / "data", tmp=tmp_path)
    runner.close()
    server.shutdown()
    server.server_close()
    store.close()


def client(hosted, who=None, code=None) -> httpx.Client:
    session = httpx.Client(base_url=hosted.base, headers={"X-Nightshift": "1"}, timeout=30)
    if who:
        body = {"email": who[0], "password": who[1], **({"code": code} if code else {})}
        response = session.post("/api/login", json=body)
        assert response.status_code == 200, response.text
    return session


def _project(admin, name="Acme Retail"):
    return admin.post("/api/projects", json={"client": name}).json()["project"]["slug"]


# --- secrets are encrypted at rest ---------------------------------------------------------

def test_secrets_are_encrypted_on_disk_and_only_open_with_their_key(hosted):
    admin = client(hosted, ADMIN)
    slug = _project(admin)
    assert admin.put(f"/api/projects/{slug}/secrets/SHOP_PASSWORD", json={"value": "fake-s3cret-value"}).status_code == 200
    files = ProjectFiles(hosted.data, slug)
    stored = files.secrets_file.read_text(encoding="utf-8")
    assert "fake-s3cret-value" not in stored and "SHOP_PASSWORD=enc:v1:" in stored
    assert files.secrets() == {"SHOP_PASSWORD": "fake-s3cret-value"}  # what a run gets
    with pytest.raises(VaultError, match="not the one it was saved with"):
        Vault(Fernet.generate_key()).open(stored.split("=", 1)[1].strip())  # another installation's key


def test_secrets_saved_before_encryption_are_encrypted_at_start(hosted):
    files = ProjectFiles(hosted.data, "older-client")
    files.secrets_file.write_text("OLD_PASSWORD=plain-fake-value\n", encoding="utf-8")
    assert files.secrets() == {"OLD_PASSWORD": "plain-fake-value"}  # still readable as it was
    assert files.seal_secrets() == 1
    assert "plain-fake-value" not in files.secrets_file.read_text(encoding="utf-8")
    assert files.secrets() == {"OLD_PASSWORD": "plain-fake-value"}
    assert files.seal_secrets() == 0


# --- password reset links ------------------------------------------------------------------

def test_an_admin_makes_a_one_time_reset_link_that_logs_the_person_out_everywhere(hosted):
    admin, staff = client(hosted, ADMIN), client(hosted, STAFF)
    staff_id = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["email"] == STAFF[0])
    assert staff.post(f"/api/users/{staff_id}/reset").status_code == 403  # staff can't make them
    made = admin.post(f"/api/users/{staff_id}/reset").json()
    token = made["path"].rsplit("/", 1)[1]
    guest = client(hosted)
    assert guest.get(f"/api/reset/{token}").json() == {"email": STAFF[0]}
    assert guest.post(f"/api/reset/{token}", json={"password": "short"}).status_code == 400
    assert guest.post(f"/api/reset/{token}", json={"password": "a-new-fake-password"}).status_code == 200
    assert guest.post(f"/api/reset/{token}", json={"password": "another-fake-password"}).status_code == 404  # spent
    assert staff.get("/api/me").status_code == 401  # the old login is gone
    assert client(hosted).post("/api/login", json={"email": STAFF[0], "password": STAFF[1]}).status_code == 401
    client(hosted, (STAFF[0], "a-new-fake-password"))


def test_an_admin_cannot_reset_someone_in_another_workspace(hosted):
    other = hosted.store.add_workspace("Other QA")
    outsider = hosted.store.add_user("outsider@other.test", "O", "outsider-fake-password", workspace_id=other["id"])
    assert client(hosted, ADMIN).post(f"/api/users/{outsider}/reset").status_code == 404


# --- two-factor login ----------------------------------------------------------------------

def test_two_factor_login_needs_a_fresh_code_from_the_app(hosted):
    staff = client(hosted, STAFF)
    secret = staff.post("/api/me/2fa").json()["secret"]
    assert staff.post("/api/me/2fa/confirm", json={"code": "000000"}).status_code == 400
    assert staff.post("/api/me/2fa/confirm", json={"code": totp(secret)}).status_code == 200
    assert staff.get("/api/me").json()["user"]["two_factor"] is True

    guest = client(hosted)
    asked = guest.post("/api/login", json={"email": STAFF[0], "password": STAFF[1]})
    assert asked.status_code == 401 and asked.json()["need_code"] is True
    assert guest.post("/api/login", json={"email": STAFF[0], "password": STAFF[1], "code": "123456"}).status_code == 401
    code = totp(secret, at=time.time() + 30)  # the confirm used this step's code; the next one is new
    assert guest.post("/api/login", json={"email": STAFF[0], "password": STAFF[1], "code": code}).status_code == 200
    replay = client(hosted).post("/api/login", json={"email": STAFF[0], "password": STAFF[1], "code": code})
    assert replay.status_code == 401  # a code works once
    # The authenticator secret is not in the database in the clear.
    hosted.store.close()
    assert secret.encode() not in (hosted.data / "nightshift.db").read_bytes()


def test_an_admin_turns_off_two_factor_for_someone_who_lost_their_phone(hosted):
    staff = client(hosted, STAFF)
    secret = staff.post("/api/me/2fa").json()["secret"]
    staff.post("/api/me/2fa/confirm", json={"code": totp(secret)})
    admin = client(hosted, ADMIN)
    staff_id = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["email"] == STAFF[0])
    assert staff.post(f"/api/users/{staff_id}/2fa-off").status_code == 403
    assert admin.post(f"/api/users/{staff_id}/2fa-off").status_code == 200
    client(hosted, STAFF)  # the password alone works again


def test_wrong_codes_count_toward_the_lockout(hosted):
    staff = client(hosted, STAFF)
    secret = staff.post("/api/me/2fa").json()["secret"]
    staff.post("/api/me/2fa/confirm", json={"code": totp(secret)})
    guest = client(hosted)
    for _ in range(5):
        guest.post("/api/login", json={"email": STAFF[0], "password": STAFF[1], "code": "111111"})
    assert guest.post("/api/login", json={"email": STAFF[0], "password": STAFF[1],
                                          "code": totp(secret, at=time.time() + 30)}).status_code == 429


# --- the activity log ----------------------------------------------------------------------

def test_every_change_is_logged_in_words_without_any_value(hosted):
    admin = client(hosted, ADMIN)
    slug = _project(admin)
    admin.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC})
    admin.put(f"/api/projects/{slug}/secrets/SHOP_PASSWORD", json={"value": "fake-s3cret-value"})
    client(hosted).post("/api/login", json={"email": STAFF[0], "password": "not-the-password"})
    events = admin.get("/api/audit").json()["events"]
    actions = [e["action"] for e in events]
    assert "set the secret SHOP_PASSWORD on acme-retail" in actions
    assert "saved the test smoke in acme-retail" in actions
    assert "added the client Acme Retail" in actions
    assert "logged in" in actions and "login failed: wrong password" in actions
    assert all(e["ip"] == "127.0.0.1" for e in events)
    hosted.store.close()
    raw = (hosted.data / "nightshift.db").read_bytes()
    assert b"fake-s3cret-value" not in raw and b"not-the-password" not in raw


def test_staff_and_other_agencies_cannot_read_the_log(hosted):
    assert client(hosted, STAFF).get("/api/audit").status_code == 403
    other = hosted.store.add_workspace("Other QA")
    hosted.store.add_user("boss@other.test", "B", "other-admin-fake-pw", admin=True, workspace_id=other["id"])
    client(hosted, ADMIN).post("/api/projects", json={"client": "Secret Client"})
    events = client(hosted, ("boss@other.test", "other-admin-fake-pw")).get("/api/audit").json()["events"]
    assert all("Secret Client" not in e["action"] for e in events)


# --- deleting a client's or an agency's data -----------------------------------------------

def test_a_client_and_everything_of_theirs_is_deleted_only_with_its_name_typed(hosted):
    admin = client(hosted, ADMIN)
    slug = _project(admin)
    admin.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC})
    folder = ProjectFiles(hosted.data, slug).root
    assert client(hosted, STAFF).post(f"/api/projects/{slug}/delete", json={"confirm": "Acme Retail"}).status_code == 403
    assert admin.post(f"/api/projects/{slug}/delete", json={"confirm": "acme"}).status_code == 400
    assert admin.post(f"/api/projects/{slug}/delete", json={"confirm": "Acme Retail"}).status_code == 200
    assert not folder.exists() and hosted.store.project(slug) is None
    assert admin.get(f"/api/projects/{slug}").status_code == 404


def test_the_owner_deletes_an_agency_with_its_people_and_clients(hosted):
    owner = client(hosted, ADMIN)  # the first user owns the deployment
    made = owner.post("/api/workspaces", json={"name": "Leaving QA", "note": "Their admin"}).json()
    workspace_id = made["workspace"]["id"]
    member = hosted.store.add_user("member@leaving.test", "M", "leaving-fake-password", admin=True,
                                   workspace_id=workspace_id)
    project = hosted.store.add_project("Their Client", workspace_id=workspace_id)
    ProjectFiles(hosted.data, project["slug"]).set_secret("THEIR_KEY", "fake")
    assert owner.post(f"/api/workspaces/{workspace_id}/delete", json={"confirm": "Leaving"}).status_code == 400
    assert owner.post(f"/api/workspaces/{workspace_id}/delete", json={"confirm": "Leaving QA"}).status_code == 200
    assert hosted.store.workspace(workspace_id) is None and hosted.store.project(project["slug"]) is None
    assert member not in [u["id"] for u in hosted.store.users()]
    assert not (hosted.data / "projects" / project["slug"]).exists()
    assert owner.post("/api/workspaces/1/delete", json={"confirm": "My agency"}).status_code == 400


# --- backups ---------------------------------------------------------------------------------

def test_a_backup_restores_into_a_working_app(hosted, tmp_path):
    admin = client(hosted, ADMIN)
    slug = _project(admin)
    admin.put(f"/api/projects/{slug}/specs/smoke", json={"text": SPEC})
    admin.put(f"/api/projects/{slug}/secrets/SHOP_PASSWORD", json={"value": "fake-s3cret-value"})
    backup = make_backup(hosted.data, tmp_path / "backups-manual")
    with zipfile.ZipFile(backup) as archive:
        names = archive.namelist()
        assert "nightshift.db" in names and f"projects/{slug}/specs/smoke.yaml" in names
        assert b"fake-s3cret-value" not in archive.read(f"projects/{slug}/secrets.env")

    restored = tmp_path / "restored"
    restore_backup(backup, restored)
    store = Store(restored / "nightshift.db")
    try:
        assert store.check_login(*ADMIN) is not None and store.project(slug)["client"] == "Acme Retail"
    finally:
        store.close()
    assert ProjectFiles(restored, slug).secrets() == {"SHOP_PASSWORD": "fake-s3cret-value"}
    with pytest.raises(BackupError, match="exists"):
        restore_backup(backup, restored)


def test_a_backup_cannot_write_outside_the_data_folder(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("nightshift.db", b"")
        archive.writestr("../outside.txt", b"x")
    with pytest.raises(BackupError, match="leaves the data folder"):
        restore_backup(evil, tmp_path / "data")
    assert not (tmp_path / "outside.txt").exists()


def test_the_server_backs_up_once_a_night_and_keeps_the_newest(hosted):
    runner = hosted.runner
    assert runner.backup_tick(datetime(2026, 10, 10, 3, 0)) is None  # not yet
    made = runner.backup_tick(datetime.now().replace(hour=23, minute=59))
    assert made is not None and made.exists()
    assert runner.backup_tick(datetime.now().replace(hour=23, minute=59)) is None  # once a day
    for _ in range(3):
        time.sleep(1.1)  # backups are named by the second
        make_backup(hosted.data, hosted.tmp / "backups", keep=2)
    assert len(list((hosted.tmp / "backups").glob("*.zip"))) == 2


# --- the health check -------------------------------------------------------------------------

def test_the_health_check_needs_no_login_and_names_nothing(hosted):
    admin = client(hosted, ADMIN)
    _project(admin, "Private Client Name")
    response = client(hosted).get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["database"] == "ok" and body["queued"] == 0
    assert "Private" not in response.text and "@" not in response.text


def test_the_log_names_the_person_an_admin_acted_on_even_after_removing_them(hosted):
    admin = client(hosted, ADMIN)
    staff_id = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["email"] == STAFF[0])
    admin.post(f"/api/users/{staff_id}/reset")
    admin.delete(f"/api/users/{staff_id}")
    actions = [e["action"] for e in admin.get("/api/audit").json()["events"]]
    assert f"made a password reset link for {STAFF[0]}" in actions and f"removed {STAFF[0]}" in actions
