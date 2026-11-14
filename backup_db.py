#!/usr/bin/env python3
"""
Scheduled backup — writes a timestamped JSON backup to disk, same format
as the Site Settings "Export full backup" button, and prunes old backups
so this doesn't slowly fill the SD card.

This exists for a different reason than the manual export button: that
one protects you when *you* remember to click it before an update. This
one protects you when you don't — a Pi's SD card can fail on its own
schedule, not just around your deploys.

NOTE: you usually don't need this any more — Site Settings -> Backups has a
built-in daily schedule (no cron). This script remains for people who prefer
cron, or want to run a backup from the command line.

Usage:
    python3 backup_db.py
    python3 backup_db.py --keep 14        # keep the last 14 backups (default: 14)
    python3 backup_db.py --dir /some/path  # where to write them (default: ./backups)

Off-Pi copies (so a dead SD card or a dead Pi doesn't take the backups with it):
    --copy-to /media/usb/vsb          copy to a folder (USB stick, NAS mount)
    --rclone gdrive:vsb-backups       upload with rclone (Google Drive, Dropbox, S3, ...)
    --rsync user@host:/backups/vsb    push over SSH with rsync
Any combination works. If a copy fails the script exits non-zero (so cron mail /
monitoring notices) — but the local backup is still written first.

Backup files contain password hashes, so they're written readable by the
owner only (0600). Keep off-Pi destinations private.

Intended to run on a schedule via cron — see install.md for the exact
crontab line.
"""
import argparse
import sys
from datetime import datetime

from app.backup_job import run_backup
from app.database import SessionLocal, init_db
from app.models import SiteSettings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="backups", help="directory to write backups into")
    parser.add_argument("--keep", type=int, default=14, help="how many recent backups to keep")
    parser.add_argument("--copy-to", action="append", default=[], metavar="DIR", help="also copy each backup into this folder (repeatable)")
    parser.add_argument("--rclone", action="append", default=[], metavar="REMOTE:PATH", help="also upload with rclone (repeatable)")
    parser.add_argument("--rsync", action="append", default=[], metavar="DEST", help="also push with rsync, e.g. user@host:/path (repeatable)")
    args = parser.parse_args()

    init_db()  # bring an older database up to date first (adds any missing columns in place)
    db = SessionLocal()
    try:
        result = run_backup(db, args.dir, args.keep, args.copy_to, args.rclone, args.rsync)
        settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
        if not settings:
            settings = SiteSettings(id=1)
            db.add(settings)
        settings.last_backup_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

    c = result.counts
    print(f"Wrote {result.path} ({c['users']} users, {c['projects']} forms, {c['submissions']} submissions, {c['survey_questions']} survey questions, {c['survey_answers']} survey answers)")
    for f in result.failures:
        print(f"FAILED {f}", file=sys.stderr)
    sys.exit(1 if result.failures else 0)
