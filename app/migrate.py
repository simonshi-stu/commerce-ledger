"""离线 SQLite 版本检查和升级；已有库升级前生成一致性备份。"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
from uuid import uuid4

from .db import Database, MIGRATIONS

ROOT = Path(__file__).resolve().parent.parent


def inspect_version(path):
    path = Path(path)
    if not path.exists():
        return 0
    uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    try:
        return source.execute("PRAGMA user_version").fetchone()[0]
    finally:
        source.close()


def migrate_database(path):
    path = Path(path)
    version, latest = inspect_version(path), MIGRATIONS[-1][0]
    if version > latest:
        raise RuntimeError("数据库版本高于当前应用，拒绝升级或降级")
    if version == latest:
        return {"from_version": version, "to_version": latest, "backup": None, "changed": False}
    backup = None
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(path.name + f".v{version}-{stamp}-{uuid4().hex[:8]}.bak")
        uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
        source = sqlite3.connect(uri, uri=True)
        destination = sqlite3.connect(str(backup))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    Database(path).initialize()
    return {"from_version": version, "to_version": latest,
            "backup": str(backup) if backup else None, "changed": True}


def main():
    parser = argparse.ArgumentParser(description="本地 SQLite 迁移；默认只检查，--apply 升级")
    parser.add_argument("--database", default=os.environ.get("COMMERCE_DB", str(ROOT / "data" / "commerce.db")))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(migrate_database(args.database))
    else:
        version = inspect_version(args.database)
        print({"exists": Path(args.database).exists(), "version": version, "supported": MIGRATIONS[-1][0]})


if __name__ == "__main__":
    main()
