"""
Writing a backup file to disk (and optionally copying it off the Pi) —
shared by the standalone script (backup_db.py), the "Back up now" button,
and the built-in daily scheduler, so all three behave identically.

The scheduler is a small background loop inside the app itself: no cron
needed. It checks once a minute whether a backup is due according to the
schedule saved in Site Settings -> Backups.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import update

from .backup import build_backup_dict
from .database import SessionLocal
from .models import SiteSettings

RETRY_AFTER = timedelta(hours=1)  # after a failed attempt, try again no sooner than this
DEFAULT_DIR = "backups"
STATIC_DIR = (Path(__file__).parent / "static").resolve()


class BackupResult:
    def __init__(self, path: Path, failures: List[str], counts: dict):
        self.path, self.failures, self.counts = path, failures, counts

    @property
    def ok(self) -> bool:
        return not self.failures


def check_destination(directory: str) -> Optional[str]:
    """None if a backup could be written there, otherwise a plain-English problem."""
    if not directory or "\0" in directory:
        return "Enter a folder"
    p = Path(directory).expanduser().resolve()
    if p == STATIC_DIR or STATIC_DIR in p.parents:
        return "That folder is served on the web — pick somewhere else"
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return f"Can't write to {p}: {e.strerror or e}"
    return None


def _prune(directory: Path, keep: int) -> int:
    existing = sorted(directory.glob("backup-*.json"), key=lambda p: p.name)
    old = existing[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
    return len(old)


def _run_tool(label: str, cmd: List[str], failures: List[str]) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            failures.append(f"{label}: {(r.stderr or r.stdout).strip()[:200] or 'failed'}")
    except FileNotFoundError:
        failures.append(f"{label}: {cmd[0]} is not installed on this machine")
    except subprocess.TimeoutExpired:
        failures.append(f"{label}: timed out")


def run_backup(db, directory: str = DEFAULT_DIR, keep: int = 14, copy_to: Iterable[str] = (),
               rclone: Iterable[str] = (), rsync: Iterable[str] = ()) -> BackupResult:
    """Writes backup-<timestamp>.json (owner-readable only), verifies it
    parses, prunes old ones, then makes any off-Pi copies. The local file is
    always written first; copy problems are reported in `failures`."""
    data = build_backup_dict(db)
    local = Path(directory).expanduser()
    local.mkdir(parents=True, exist_ok=True)
    name = f"backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    path = local / name
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)
    json.loads(path.read_text())  # a backup you can't parse is not a backup
    _prune(local, keep)

    failures: List[str] = []
    for dest in copy_to:
        try:
            d = Path(dest).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, d / name)
            os.chmod(d / name, 0o600)
            _prune(d, keep)
        except OSError as e:
            failures.append(f"{dest}: {e.strerror or e}")
    for remote in rclone:
        _run_tool(remote, ["rclone", "copy", str(path), remote], failures)
    for dest in rsync:
        _run_tool(dest, ["rsync", "-a", str(path), dest], failures)

    counts = {k: len(data[k]) for k in ("users", "projects", "submissions", "survey_questions", "survey_answers")}
    return BackupResult(path, failures, counts)


def _targets(s: SiteSettings) -> Tuple[List[str], List[str], List[str]]:
    one = lambda v: [v.strip()] if v and v.strip() else []
    return one(s.backup_copy_dir), one(s.backup_rclone), one(s.backup_rsync)


def run_and_record(db, s: SiteSettings) -> BackupResult:
    """Runs a backup with the saved settings and stores the outcome on them."""
    copy_to, rclone, rsync = _targets(s)
    now = datetime.utcnow()
    s.backup_last_attempt_at = now
    try:
        result = run_backup(db, s.backup_dir or DEFAULT_DIR, s.backup_keep or 14, copy_to, rclone, rsync)
    except Exception as e:  # disk full, permissions, ...
        s.backup_last_error = f"Backup failed: {e}"[:300]
        db.commit()
        raise
    s.backup_last_ok_at = now  # the local file is safely written
    s.last_backup_at = now
    s.backup_last_error = ("Saved here, but an off-Pi copy failed — " + "; ".join(result.failures))[:300] if result.failures else None
    db.commit()
    return result


def most_recent_slot(hour: int, now_local: datetime) -> datetime:
    """The latest scheduled moment (today at `hour`, or yesterday's if that's still ahead)."""
    slot = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
    return slot if now_local >= slot else slot - timedelta(days=1)


def is_due(s: SiteSettings, now_utc: Optional[datetime] = None) -> bool:
    if not s.backup_enabled:
        return False
    now_utc = now_utc or datetime.utcnow()
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone()  # server's own timezone
    slot_utc = most_recent_slot(s.backup_hour if s.backup_hour is not None else 2, now_local) \
        .astimezone(timezone.utc).replace(tzinfo=None)
    if s.backup_last_ok_at and s.backup_last_ok_at >= slot_utc:
        return False
    attempt = s.backup_last_attempt_at
    return not (attempt and attempt >= slot_utc and now_utc - attempt < RETRY_AFTER)


def run_if_due() -> None:
    """One scheduler tick. Claims the attempt with an atomic UPDATE first, so
    even if two server processes were running only one would do the backup."""
    db = SessionLocal()
    try:
        s = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
        if not s or not is_due(s):
            return
        seen = s.backup_last_attempt_at
        claimed = db.execute(
            update(SiteSettings)
            .where(SiteSettings.id == 1, SiteSettings.backup_last_attempt_at.is_(None) if seen is None
                   else SiteSettings.backup_last_attempt_at == seen)
            .values(backup_last_attempt_at=datetime.utcnow())
        ).rowcount
        db.commit()
        if not claimed:
            return
        db.refresh(s)
        try:
            run_and_record(db, s)
        except Exception as e:
            print(f"[backup] scheduled backup failed: {e}", file=sys.stderr, flush=True)
    finally:
        db.close()


async def scheduler_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_if_due)
        except Exception as e:  # never let the loop die
            print(f"[backup] scheduler error: {e}", file=sys.stderr, flush=True)
        await asyncio.sleep(60)
