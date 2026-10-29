"""
In-place, additive schema migration — run automatically at startup.

WHY THIS EXISTS
SQLAlchemy's create_all() creates missing *tables* but never alters existing
ones, so every time a model gained a column the old workflow was: export a
backup, delete suggestionbox.db, restart, re-import. That workflow is only as
good as the backup format, and it destroyed data more than once (a backup
that doesn't know about a new table or column silently drops it).

This removes the need for it. At startup, for every table the models define
that already exists in the database, any column the model has but the
database lacks is added with ALTER TABLE ... ADD COLUMN. Existing rows are
never rewritten, moved, or deleted.

SAFETY
- Additive only. It never drops, renames, retypes, or rewrites anything.
- Before the first ALTER it writes a consistent copy of the database file to
  <name>.before-migration-<timestamp> (SQLite online-backup API).
- A column it cannot add safely (NOT NULL with no constant default, or
  UNIQUE / primary key) is NOT added silently: it raises, so the app refuses
  to start with a clear message rather than running against a half-migrated
  schema. That case needs a deliberate, hand-written migration.
- Idempotent: once the columns exist there is nothing to do and no copy is made.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

from .database import Base


def _log(msg: str):
    print(f"[migrate] {msg}", flush=True)


def _sqlite_default_sql(column) -> str | None:
    """A literal DEFAULT clause for a constant Python-side default, else None."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _plan(engine):
    """[(table, column)] the models have but the existing database lacks."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    pending = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all() handles whole missing tables
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in have:
                pending.append((table, column))
    return pending


def _safety_copy(engine) -> Path | None:
    db_file = engine.url.database
    if not db_file or db_file == ":memory:":
        return None
    src_path = Path(db_file).resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_path = src_path.with_name(f"{src_path.name}.before-migration-{stamp}")
    n = 1
    while dest_path.exists():  # never overwrite an earlier safety copy
        dest_path = src_path.with_name(f"{src_path.name}.before-migration-{stamp}-{n}")
        n += 1
    src = sqlite3.connect(str(src_path))
    dest = sqlite3.connect(str(dest_path))
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return dest_path


def add_missing_columns(engine) -> list[str]:
    """Adds any model column the database lacks. Returns what it did."""
    pending = _plan(engine)
    if not pending:
        return []

    # Validate everything up front so we never half-migrate.
    statements = []
    for table, column in pending:
        if column.primary_key or column.unique:
            raise RuntimeError(
                f"Can't auto-add {table.name}.{column.name}: primary-key/unique columns "
                "need a hand-written migration (SQLite can't ADD them in place)."
            )
        col_type = column.type.compile(dialect=engine.dialect)
        default_sql = _sqlite_default_sql(column)
        ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
        if not column.nullable:
            if default_sql is None:
                raise RuntimeError(
                    f"Can't auto-add NOT NULL column {table.name}.{column.name}: it has no constant "
                    "default, so existing rows would have no valid value. Give the model column a "
                    "constant default= (or make it nullable), or write a migration by hand."
                )
            ddl += f" NOT NULL DEFAULT {default_sql}"
        elif default_sql is not None:
            ddl += f" DEFAULT {default_sql}"
        statements.append((table.name, column.name, ddl))

    backup = _safety_copy(engine)
    if backup:
        _log(f"schema is behind the code — safety copy written to {backup.name}")

    done = []
    with engine.begin() as conn:
        for table_name, column_name, ddl in statements:
            conn.execute(text(ddl))
            done.append(f"{table_name}.{column_name}")
            _log(f"added column {table_name}.{column_name}")
    _log(f"done — {len(done)} column(s) added, no existing data was modified")
    return done


def retire_done_status(engine):
    """The 'done' review status was retired — fold any existing 'done'
    submissions into 'reviewed'. Idempotent; writes a safety copy first
    only when there is something to change."""
    with engine.begin() as conn:
        try:
            n = conn.execute(text("SELECT COUNT(*) FROM submissions WHERE status = 'done'")).scalar()
        except Exception:
            return 0
    if not n:
        return 0
    backup = _safety_copy(engine)
    if backup:
        _log(f"retiring 'done' status — safety copy written to {backup.name}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE submissions SET status = 'reviewed' WHERE status = 'done'"))
    _log(f"moved {n} 'done' submission(s) to 'reviewed'")
    return n
