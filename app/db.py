"""每个操作独立连接；所有写操作在一个明确的事务内完成。"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from urllib.parse import quote

MIGRATIONS = (
    (1, Path(__file__).with_name("schema.sql")),
    (2, Path(__file__).with_name("migrations") / "002_history_guards.sql"),
)


class Database:
    def __init__(self, path, read_only=False):
        self.path = str(path)
        self.read_only = read_only

    def connect(self):
        if self.read_only:
            uri = "file:" + quote(Path(self.path).resolve().as_posix(), safe="/:") + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self, allow_upgrade=True):
        if self.read_only:
            raise RuntimeError("read-only databases cannot be initialized or migrated")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            latest = MIGRATIONS[-1][0]
            if version > latest:
                raise RuntimeError("数据库版本高于当前应用支持版本，请使用匹配版本的应用")
            if 0 < version < latest and not allow_upgrade:
                raise RuntimeError("已有数据库需要升级：先停止服务，执行 python -m app.migrate --apply")
            connection.execute("PRAGMA journal_mode=WAL")
            pending = [path.read_text(encoding="utf-8") for number, path in MIGRATIONS if number > version]
            if pending:
                try:
                    # executescript 自身会隐式提交已有事务，因此把 BEGIN/COMMIT 包进同一个脚本。
                    connection.executescript("BEGIN IMMEDIATE;\n" + "\n".join(pending)
                                             + f"\nPRAGMA user_version={latest};\nCOMMIT;")
                except BaseException:
                    connection.rollback()
                    raise

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def read_snapshot(self):
        """一次核对中的多条查询读取同一快照，并在连接级禁止写入。"""
        with self.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def transaction(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

