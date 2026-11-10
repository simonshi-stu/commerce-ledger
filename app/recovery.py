"""Verified SQLite snapshots and recovery rehearsal; never replace an existing file."""
import argparse
from functools import lru_cache
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from .db import Database, MIGRATIONS
from .service import check_integrity


class VerificationError(ValueError):
    pass


def schema_contract(connection):
    """Return application DDL, excluding SQLite-owned bookkeeping objects.

    Compare SQL verbatim rather than deleting whitespace, which could change
    quoted literals. This intentionally rejects independently rewritten schemas,
    even when their author believes them to be semantically equivalent.
    """
    return tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
    ))


@lru_cache(maxsize=1)
def expected_schema_contract():
    """Build the reference from this release's trusted migrations, never input DDL."""
    connection = sqlite3.connect(":memory:")
    try:
        for _, migration in MIGRATIONS:
            connection.executescript(migration.read_text(encoding="utf-8"))
        return schema_contract(connection)
    finally:
        connection.close()


def publish_new_file(staged, output):
    # Windows rename refuses an existing destination; POSIX rename replaces it.
    # POSIX hard-link creation provides the required no-clobber behavior instead.
    if os.name == "nt":
        os.rename(staged, output)
    else:
        os.link(staged, output)


def verify_database(path):
    """Read an existing file without creating it, migrating it, or repairing data."""
    db = Database(path, read_only=True)
    with db.read_snapshot() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != MIGRATIONS[-1][0]:
            raise VerificationError("database version must match this application; migrate a separate copy first")
        if schema_contract(connection) != expected_schema_contract():
            raise VerificationError("database schema does not match this application; review a separate copy first")
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise VerificationError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise VerificationError("foreign key check failed")
    # The staged backup is private and has no writer. Live verify is diagnostic:
    # these structural and business checks run in separate read snapshots.
    report = check_integrity(db, limit=20)
    if not report["healthy"]:
        raise VerificationError("business reconciliation failed")
    return {"verified": True, "database_version": version,
            "products_checked": report["products_checked"],
            "orders_checked": report["orders_checked"]}


def snapshot_database(source, output, timeout=10.0):
    """Copy committed SQLite state, verify privately, then publish with no overwrite.

    The sibling staging directory keeps publication on one filesystem. Publication
    fails if output already exists, including a concurrent creator. POSIX requires
    hard-link support; there is no overwrite fallback.
    """
    source, output = Path(source).resolve(), Path(output).absolute()
    if source == output.resolve() or output.exists() or output.is_symlink():
        raise FileExistsError("output must be a new path; existing files are never replaced")
    if not output.parent.is_dir():
        raise FileNotFoundError("output parent directory must already exist")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise TimeoutError("backup deadline exceeded; retry when database is less busy")

    with tempfile.TemporaryDirectory(prefix=".commerce-snapshot-", dir=output.parent) as directory:
        staged = Path(directory) / "snapshot.db"
        with Database(source, read_only=True).connection() as origin:
            destination = sqlite3.connect(staged)
            try:
                origin.backup(destination, pages=128, progress=progress, sleep=0.01)
            finally:
                destination.close()
        report = verify_database(staged)
        # No database handles remain when the validated file is published.
        publish_new_file(staged, output)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verified local SQLite backup and recovery rehearsal")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore"):
        command = commands.add_parser(name, help="create a verified snapshot at a new path")
        command.add_argument("--source", required=True)
        command.add_argument("--output", required=True)
    command = commands.add_parser("verify", help="read-only structure and business checks")
    command.add_argument("--database", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            report = verify_database(args.database)
        else:
            report = snapshot_database(args.source, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Do not echo filesystem paths, data, or raw SQLite errors in CLI output.
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    print(json.dumps({"ok": True, "operation": args.command, **report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
