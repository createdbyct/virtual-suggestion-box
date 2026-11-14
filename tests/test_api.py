"""
End-to-end API tests. Starts the real app against a throwaway database in a
temp folder (your real suggestionbox.db is never touched) and drives it over
HTTP, so what's tested is what runs on the Pi.

Run from the project folder, with the project's virtualenv:

    python -m unittest discover -s tests -v

Standard library only — no extra packages to install.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Client:
    """A tiny cookie-keeping HTTP client (one per 'person')."""

    def __init__(self, base):
        self.base = base
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req) as r:
                raw, status, headers = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            raw, status, headers = e.read(), e.code, e.headers
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw
        return Resp(status, payload, headers)

    def get(self, p): return self.call("GET", p)
    def post(self, p, b=None): return self.call("POST", p, b if b is not None else {})
    def patch(self, p, b): return self.call("PATCH", p, b)
    def delete(self, p): return self.call("DELETE", p)


class Resp:
    def __init__(self, status, json_, headers):
        self.status, self.json, self.headers = status, json_, headers

    @property
    def code(self):
        return self.headers.get("X-Error-Code")


class ApiTestCase(unittest.TestCase):
    proc = None
    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cls.log = open(Path(cls.tmp.name) / "server.log", "w")
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
            cwd=cls.tmp.name, env=env, stdout=cls.log, stderr=cls.log,
        )
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/login", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("test server didn't start:\n" + (Path(cls.tmp.name) / "server.log").read_text())
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            cls.proc.wait(10)
        cls.log.close()
        cls.tmp.cleanup()

    # -- helpers --
    def person(self, label="p"):
        type(self).n += 1
        name = f"{label}{self.n}"
        c = Client(self.base)
        r = c.post("/api/auth/register", {"name": name, "username": name, "email": f"{name}@example.com", "password": "password123"})
        self.assertEqual(r.status, 200, r.json)
        c.username, c.id = name, r.json["id"]
        return c

    def make_superadmin(self):
        c = self.person("root")
        subprocess.run([sys.executable, str(ROOT / "promote_admin.py"), c.username, "superadmin"],
                       cwd=self.tmp.name, env=dict(os.environ, PYTHONPATH=str(ROOT)), check=True, capture_output=True)
        # roles are read per request, so the existing session is now a superadmin
        return c

    def form(self, owner, title="Form", kind="nomination"):
        r = owner.post("/api/projects", {"title": title, "type": kind})
        self.assertEqual(r.status, 200, r.json)
        return r.json

    def submit(self, slug, **kw):
        body = {"suggestion_text": "A suggestion"} | kw
        return Client(self.base).post(f"/api/vb/{slug}/submit", body)

    def share(self, owner, project, who, role="reviewer"):
        return owner.post(f"/api/projects/{project['id']}/shares", {"identifier": who.username, "role": role})


class RoleTests(ApiTestCase):
    def setUp(self):
        self.owner = self.person("owner")
        self.viewer, self.reviewer, self.editor, self.stranger = (self.person(x) for x in ("viewer", "reviewer", "editor", "stranger"))
        self.p = self.form(self.owner, kind="both")
        self.pid = self.p["id"]
        for who, role in ((self.viewer, "viewer"), (self.reviewer, "reviewer"), (self.editor, "editor")):
            self.assertEqual(self.share(self.owner, self.p, who, role).status, 200)

    def test_default_role_is_reviewer_and_owner_role_cannot_be_granted(self):
        other = self.person("other")
        r = self.owner.post(f"/api/projects/{self.pid}/shares", {"identifier": other.username})
        self.assertEqual(r.json["role"], "reviewer")
        third = self.person("third")
        self.assertEqual(self.owner.post(f"/api/projects/{self.pid}/shares", {"identifier": third.username, "role": "owner"}).status, 422)

    def test_my_role_in_form_list(self):
        for who, role in ((self.viewer, "viewer"), (self.editor, "editor"), (self.owner, "owner")):
            forms = who.get("/api/projects").json
            self.assertEqual([f["my_role"] for f in forms if f["id"] == self.pid], [role])

    def test_stranger_sees_nothing(self):
        self.assertEqual(self.stranger.get(f"/api/projects/{self.pid}/submissions").status, 404)

    def test_questions_need_editor(self):
        q = {"question_text": "Q?", "question_type": "short_text"}
        for who in (self.viewer, self.reviewer):
            r = who.post(f"/api/projects/{self.pid}/questions", q)
            self.assertEqual((r.status, r.code), (403, "insufficient_role"))
        self.assertEqual(self.editor.post(f"/api/projects/{self.pid}/questions", q).status, 200)

    def test_editor_can_retitle_but_not_publish(self):
        self.assertEqual(self.editor.patch(f"/api/projects/{self.pid}", {"title": "Renamed"}).status, 200)
        r = self.editor.patch(f"/api/projects/{self.pid}", {"is_archived": True})
        self.assertEqual((r.status, r.code), (403, "insufficient_role"))
        self.assertEqual(self.viewer.patch(f"/api/projects/{self.pid}", {"title": "X"}).status, 403)

    def test_owner_only_actions(self):
        for who in (self.viewer, self.reviewer, self.editor):
            self.assertEqual(who.get(f"/api/projects/{self.pid}/shares").status, 403)
            self.assertEqual(who.delete(f"/api/projects/{self.pid}").status, 403)
            self.assertEqual(who.post(f"/api/projects/{self.pid}/transfer-ownership", {"identifier": who.username}).status, 403)

    def test_review_stamps_who_and_when(self):
        self.assertEqual(self.submit(self.p["slug"]).status, 200)
        sid = self.owner.get(f"/api/projects/{self.pid}/submissions").json["submissions"][0]["id"]
        path = f"/api/projects/{self.pid}/submissions/{sid}/status"
        r = self.viewer.patch(path, {"status": "reviewed"})
        self.assertEqual((r.status, r.code), (403, "insufficient_role"))
        r = self.reviewer.patch(path, {"status": "reviewed"})
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json["reviewed_by_name"], self.reviewer.username)
        self.assertTrue(r.json["reviewed_at"])
        listed = self.owner.get(f"/api/projects/{self.pid}/submissions").json["submissions"][0]
        self.assertEqual(listed["reviewed_by_name"], self.reviewer.username)
        r = self.owner.patch(path, {"status": "new"})
        self.assertIsNone(r.json["reviewed_by_name"])

    def test_only_owner_deletes_responses(self):
        self.submit(self.p["slug"])
        sid = self.owner.get(f"/api/projects/{self.pid}/submissions").json["submissions"][0]["id"]
        for who in (self.viewer, self.reviewer, self.editor):
            self.assertEqual(who.delete(f"/api/projects/{self.pid}/submissions/{sid}").status, 403)
        self.assertEqual(self.owner.delete(f"/api/projects/{self.pid}/submissions/{sid}").status, 200)

    def test_changing_a_role_takes_effect_immediately(self):
        q = {"question_text": "Q?", "question_type": "short_text"}
        self.assertEqual(self.editor.post(f"/api/projects/{self.pid}/questions", q).status, 200)
        r = self.owner.patch(f"/api/projects/{self.pid}/shares/{self.editor.id}", {"role": "viewer"})
        self.assertEqual(r.json["role"], "viewer")
        self.assertEqual(self.editor.post(f"/api/projects/{self.pid}/questions", q).status, 403)
        self.owner.delete(f"/api/projects/{self.pid}/shares/{self.editor.id}")
        self.assertEqual(self.editor.get(f"/api/projects/{self.pid}/submissions").status, 404)


class SubmissionRuleTests(ApiTestCase):
    def test_nomination_requires_a_reason(self):
        owner = self.person("owner")
        p = self.form(owner, kind="nomination")
        r = self.submit(p["slug"], suggestion_text=None, nominees=[{"name": "Pat"}])
        self.assertEqual(r.status, 400)
        r = self.submit(p["slug"], suggestion_text=None, nominees=[{"name": "Pat", "reason": "Stayed late to help"}])
        self.assertEqual(r.status, 200)

    def test_done_status_is_gone(self):
        owner = self.person("owner")
        p = self.form(owner, kind="suggestion")
        self.submit(p["slug"])
        sub = owner.get(f"/api/projects/{p['id']}/submissions").json["submissions"][0]
        self.assertEqual(sub["next_statuses"], ["reviewed"])
        # The retired "done" is still accepted from old clients — as "reviewed".
        r = owner.patch(f"/api/projects/{p['id']}/submissions/{sub['id']}/status", {"status": "done"})
        self.assertEqual((r.status, r.json["status"]), (200, "reviewed"))
        r = owner.patch(f"/api/projects/{p['id']}/submissions/{sub['id']}/status", {"status": "bogus"})
        self.assertEqual(r.status, 422)


class PasswordTests(ApiTestCase):
    def test_admin_reset_forces_a_new_password(self):
        admin = self.make_superadmin()
        user = self.person("victim")
        r = admin.post(f"/api/admin/users/{user.id}/reset-password")
        self.assertEqual(r.status, 200)
        temp = r.json["temporary_password"]

        # old password and old session are dead
        self.assertEqual(user.get("/api/auth/me").status, 401)
        fresh = Client(self.base)
        self.assertEqual(fresh.post("/api/auth/login", {"identifier": user.username, "password": "password123"}).status, 401)

        r = fresh.post("/api/auth/login", {"identifier": user.username, "password": temp})
        self.assertEqual(r.status, 200)
        self.assertTrue(r.json["must_change_password"])

        # locked to the change-password screen
        self.assertEqual(fresh.get("/api/auth/me").status, 200)
        r = fresh.get("/api/projects")
        self.assertEqual((r.status, r.code), (403, "password_change_required"))
        r = self.form_attempt(fresh)
        self.assertEqual((r.status, r.code), (403, "password_change_required"))

        # can't keep the temp password
        r = fresh.post("/api/auth/change-password", {"current_password": temp, "new_password": temp})
        self.assertEqual(r.status, 400)
        r = fresh.post("/api/auth/change-password", {"current_password": temp, "new_password": "my-own-secret-1"})
        self.assertEqual(r.status, 200)

        self.assertFalse(fresh.get("/api/auth/me").json["must_change_password"])
        self.assertEqual(fresh.get("/api/projects").status, 200)

    def form_attempt(self, c):
        return c.post("/api/projects", {"title": "x", "type": "suggestion"})

    def test_normal_change_password_needs_current(self):
        u = self.person("chg")
        self.assertEqual(u.post("/api/auth/change-password", {"current_password": "wrong-one", "new_password": "another-pass-1"}).code, "wrong_password")
        self.assertEqual(u.post("/api/auth/change-password", {"current_password": "password123", "new_password": "another-pass-1"}).status, 200)

    def test_regular_user_cannot_reset_passwords(self):
        a, b = self.person("usr"), self.person("tgt")
        self.assertEqual(a.post(f"/api/admin/users/{b.id}/reset-password").status, 403)


class AuditLogTests(ApiTestCase):
    def test_actions_are_recorded_and_viewer_is_superadmin_only(self):
        admin = self.make_superadmin()
        owner, other = self.person("owner"), self.person("other")
        p = self.form(owner, title="Audited form")
        self.share(owner, p, other, "viewer")
        owner.patch(f"/api/projects/{p['id']}/shares/{other.id}", {"role": "editor"})
        owner.delete(f"/api/projects/{p['id']}/shares/{other.id}")
        owner.delete(f"/api/projects/{p['id']}")
        admin.post(f"/api/admin/users/{other.id}/reset-password")

        self.assertEqual(owner.get("/api/admin/audit-log").status, 403)
        log = admin.get("/api/admin/audit-log?limit=500").json
        actions = [e["action"] for e in log]
        for a in ("share_added", "share_role_changed", "share_removed", "form_deleted", "password_reset_by_admin"):
            self.assertIn(a, actions)
        entry = next(e for e in log if e["action"] == "share_role_changed")
        self.assertIn(owner.username, entry["actor_name"])
        self.assertIn("viewer -> editor", entry["detail"])
        blob = json.dumps(log)
        self.assertNotIn("temporary_password", blob)
        self.assertEqual([e["action"] for e in admin.get("/api/admin/audit-log?action=form_deleted").json], ["form_deleted"] * actions.count("form_deleted"))


class BackupTests(ApiTestCase):
    def test_export_includes_roles_reviews_and_audit_and_restores(self):
        admin = self.make_superadmin()
        owner, rv = self.person("owner"), self.person("rv")
        p = self.form(owner, kind="suggestion")
        self.share(owner, p, rv, "editor")
        self.submit(p["slug"])
        sid = owner.get(f"/api/projects/{p['id']}/submissions").json["submissions"][0]["id"]
        rv.patch(f"/api/projects/{p['id']}/submissions/{sid}/status", {"status": "reviewed"})

        r = admin.get("/api/admin/export")
        self.assertEqual(r.status, 200)
        data = r.json
        self.assertIn("editor", [s["role"] for s in data["project_shares"]])
        self.assertTrue(any(s.get("reviewed_by_id") for s in data["submissions"]))
        self.assertIn("share_added", [e["action"] for e in data["audit_log"]])

        r = admin.post("/api/admin/import", data)
        self.assertEqual(r.status, 200, r.json)
        again = Client(self.base)
        again.post("/api/auth/login", {"identifier": owner.username, "password": "password123"})
        forms = again.get("/api/projects").json
        self.assertTrue(any(f["id"] == p["id"] for f in forms))


class BackupScheduleTests(ApiTestCase):
    def test_schedule_save_validate_and_run_now(self):
        admin = self.make_superadmin()
        user = self.person("plain")
        self.assertEqual(user.get("/api/admin/backup-schedule").status, 403)

        dest = Path(self.tmp.name) / "stick"
        local = Path(self.tmp.name) / "mine"
        body = {"enabled": True, "hour": 3, "keep": 3, "directory": str(local), "copy_dir": str(dest)}
        r = admin.call("PUT", "/api/admin/backup-schedule", body)
        self.assertEqual(r.status, 200, r.json)
        self.assertEqual((r.json["enabled"], r.json["hour"], r.json["keep"]), (True, 3, 3))
        self.assertTrue(r.json["server_time"])

        for _ in range(4):
            r = admin.post("/api/admin/backup-now")
            self.assertEqual(r.status, 200, r.json)
            time.sleep(1.1)  # filenames are per-second
        self.assertIsNone(r.json["last_error"])
        self.assertTrue(r.json["last_ok_at"])
        self.assertEqual(len(list(local.glob("backup-*.json"))), 3)   # pruned to "keep"
        self.assertEqual(len(list(dest.glob("backup-*.json"))), 3)    # copied + pruned off-Pi
        self.assertEqual(oct(next(local.glob("backup-*.json")).stat().st_mode & 0o777), "0o600")

    def test_bad_input_is_rejected(self):
        admin = self.make_superadmin()
        ok = {"enabled": True, "hour": 2, "keep": 14}
        put = lambda **kw: admin.call("PUT", "/api/admin/backup-schedule", ok | kw)
        self.assertEqual(put(hour=24).status, 422)
        self.assertEqual(put(keep=0).status, 422)
        self.assertEqual(put(rclone="--config=/etc/passwd").status, 422)   # no option injection
        blocker = Path(self.tmp.name) / "afile"
        blocker.write_text("x")
        r = put(copy_dir=str(blocker / "sub"))
        self.assertEqual(r.status, 400)
        self.assertIn("Second folder", r.json["detail"])
        r = put(directory=str(ROOT / "app" / "static" / "x"))
        self.assertEqual(r.status, 400)

    def test_failing_off_pi_copy_is_reported_but_local_backup_kept(self):
        admin = self.make_superadmin()
        local = Path(self.tmp.name) / "mine2"
        admin.call("PUT", "/api/admin/backup-schedule", {"enabled": False, "directory": str(local), "rclone": "nosuchremote:x"})
        r = admin.post("/api/admin/backup-now")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.json["last_error"])
        self.assertEqual(len(list(local.glob("backup-*.json"))), 1)


class SchedulerDueTests(unittest.TestCase):
    """The 'is a backup due?' rule, with no server involved."""
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        from datetime import datetime
        from types import SimpleNamespace
        from app import backup_job
        self.job, self.dt, self.S = backup_job, datetime, SimpleNamespace
        os.environ.setdefault("TZ", "UTC")

    def settings(self, **kw):
        base = dict(backup_enabled=True, backup_hour=2, backup_last_ok_at=None, backup_last_attempt_at=None)
        return self.S(**(base | kw))

    def at(self, h, day=14):
        return self.dt(2026, 11, day, h, 0, 0)

    def test_off_means_never(self):
        self.assertFalse(self.job.is_due(self.settings(backup_enabled=False), self.at(12)))

    def test_due_after_the_slot_if_not_done_today(self):
        local_hour = lambda utc: utc.replace(tzinfo=__import__("datetime").timezone.utc).astimezone().hour
        now = self.at(12)
        s = self.settings(backup_hour=local_hour(self.at(2)))
        self.assertTrue(self.job.is_due(s, now))
        s.backup_last_ok_at = self.at(3)
        self.assertFalse(self.job.is_due(s, now))                      # already done today
        self.assertTrue(self.job.is_due(s, self.at(12, day=15)))       # but due again tomorrow

    def test_not_due_before_todays_slot_if_yesterdays_done(self):
        local_hour = lambda utc: utc.replace(tzinfo=__import__("datetime").timezone.utc).astimezone().hour
        s = self.settings(backup_hour=local_hour(self.at(20)), backup_last_ok_at=self.at(21, day=13))
        self.assertFalse(self.job.is_due(s, self.at(10)))

    def test_failed_attempt_waits_an_hour(self):
        local_hour = lambda utc: utc.replace(tzinfo=__import__("datetime").timezone.utc).astimezone().hour
        s = self.settings(backup_hour=local_hour(self.at(2)), backup_last_attempt_at=self.at(11))
        self.assertFalse(self.job.is_due(s, self.dt(2026, 11, 14, 11, 30)))
        self.assertTrue(self.job.is_due(s, self.dt(2026, 11, 14, 12, 5)))


class BackupScriptTests(unittest.TestCase):
    def test_backup_script_writes_private_file_and_off_pi_copy(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "usb"
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            r = subprocess.run([sys.executable, str(ROOT / "backup_db.py"), "--dir", str(Path(work) / "local"),
                                "--copy-to", str(dest)], cwd=work, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            local = list((Path(work) / "local").glob("backup-*.json"))
            copied = list(dest.glob("backup-*.json"))
            self.assertEqual(len(local), 1)
            self.assertEqual(len(copied), 1)
            self.assertEqual(oct(local[0].stat().st_mode & 0o777), "0o600")
            json.loads(copied[0].read_text())

    def test_failed_off_pi_copy_exits_nonzero_but_keeps_local_backup(self):
        with tempfile.TemporaryDirectory() as work:
            blocker = Path(work) / "file"
            blocker.write_text("x")  # a file where a folder is needed -> copy fails
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            r = subprocess.run([sys.executable, str(ROOT / "backup_db.py"), "--dir", str(Path(work) / "local"),
                                "--copy-to", str(blocker / "sub")], cwd=work, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("FAILED", r.stderr)
            self.assertEqual(len(list((Path(work) / "local").glob("backup-*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
