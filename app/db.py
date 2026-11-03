"""每个操作独立连接；所有写操作在一个明确的事务内完成。"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Database:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("数据库版本高于当前应用支持版本，请使用匹配版本的应用")
            if version == 0:
                schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
                try:
                    connection.executescript("BEGIN IMMEDIATE;\n" + schema + "\nPRAGMA user_version=1;\nCOMMIT;")
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

