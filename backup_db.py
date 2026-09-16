#!/usr/bin/env python3
"""
Scheduled backup — writes a timestamped JSON backup to disk, same format
as the Site Settings "Export full backup" button, and prunes old backups
so this doesn't slowly fill the SD card.

This exists for a different reason than the manual export button: that
one protects you when *you* remember to click it before an update. This
one protects you when you don't — a Pi's SD card can fail on its own
schedule, not just around your deploys.

Usage:
    python3 backup_db.py
    python3 backup_db.py --keep 14        # keep the last 14 backups (default: 14)
    python3 backup_db.py --dir /some/path  # where to write them (default: ./backups)

Intended to run on a schedule via cron — see install.md for the exact
crontab line.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from app.backup import build_backup_dict
from app.database import SessionLocal
from app.models import SiteSettings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="backups", help="directory to write backups into")
    parser.add_argument("--keep", type=int, default=14, help="how many recent backups to keep")
    args = parser.parse_args()

    backup_dir = Path(args.dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        data = build_backup_dict(db)

        settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
        if not settings:
            settings = SiteSettings(id=1)
            db.add(settings)
        settings.last_backup_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

    filename = f"backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    filepath = backup_dir / filename
    filepath.write_text(json.dumps(data, indent=2))

    print(f"Wrote {filepath} ({len(data['users'])} users, {len(data['projects'])} forms, {len(data['submissions'])} submissions)")

    # Prune down to the most recent N backups, oldest first.
    existing = sorted(backup_dir.glob("backup-*.json"), key=lambda p: p.name)
    to_delete = existing[:-args.keep] if args.keep > 0 else []
    for old_file in to_delete:
        old_file.unlink()
    if to_delete:
        print(f"Pruned {len(to_delete)} old backup(s), keeping the {args.keep} most recent")

    sys.exit(0)
