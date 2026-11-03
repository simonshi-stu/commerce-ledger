import sqlite3

import pytest

from app import recovery, service
from app.db import Database
from app.schemas import Adjustment, OrderCreate, ProductCreate


@pytest.fixture
def populated(tmp_path):
    db = Database(tmp_path / "source.db")
    db.initialize()
    product = service.create_product(db, ProductCreate(sku="RESTORE-001", name="synthetic", price_cents=2990))
    service.adjust_stock(db, product["id"], Adjustment(delta=5, request_key="restore-stock-001", reason="synthetic"))
    payload = OrderCreate(request_key="restore-order-001", items=[{"product_id": product["id"], "quantity": 2}])
    order = service.place_order(db, payload)
    return db, product["id"], payload, order


def test_backup_restore_preserves_price_stock_idempotency_and_guards(populated, tmp_path):
    db, pid, payload, order = populated
    backup, restored = tmp_path / "snapshot.db", tmp_path / "restored.db"
    assert recovery.snapshot_database(db.path, backup)["orders_checked"] == 1
    service.transition_order(db, order["id"], "CANCELLED")
    recovery.snapshot_database(backup, restored)
    recovered = Database(restored)
    assert service.place_order(recovered, payload) == order
    with recovered.connection() as c:
        assert service.require_product(c, pid)["available"] == 3
        assert c.execute("SELECT count(*) FROM stock_movements WHERE kind='SALE'").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM stock_movements")
    assert service.transition_order(recovered, order["id"], "CANCELLED")["status"] == "CANCELLED"
    assert recovery.verify_database(restored)["verified"]
    # The original remains independently cancelled; recovery does not switch the API.
    with db.connection() as c:
        assert service.order_detail(c, order["id"])["status"] == "CANCELLED"


def test_online_backup_includes_committed_wal_but_not_uncommitted_write(populated, tmp_path):
    db, pid, _, _ = populated
    with db.connection() as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE products SET name='committed WAL value' WHERE id=?", (pid,))
        assert (tmp_path / "source.db-wal").stat().st_size > 0
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE products SET name='uncommitted value' WHERE id=?", (pid,))
        backup = tmp_path / "wal-backup.db"
        recovery.snapshot_database(db.path, backup)
        writer.rollback()
    with Database(backup, read_only=True).connection() as c:
        assert service.require_product(c, pid)["name"] == "committed WAL value"


@pytest.mark.parametrize("operation", ["verify", "backup"])
def test_missing_source_is_not_created(operation, tmp_path):
    source, target = tmp_path / "missing.db", tmp_path / "target.db"
    with pytest.raises(sqlite3.OperationalError):
        if operation == "verify":
            recovery.verify_database(source)
        else:
            recovery.snapshot_database(source, target)
    assert not source.exists() and not target.exists()
    assert list(tmp_path.glob(".commerce-snapshot-*")) == []


@pytest.mark.parametrize("target_name", ["source.db", "existing.db"])
def test_existing_target_never_changes(populated, tmp_path, target_name):
    db, _, _, _ = populated
    target = tmp_path / target_name
    if not target.exists():
        target.write_bytes(b"preserve this file")
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        recovery.snapshot_database(db.path, target)
    assert target.read_bytes() == original


def test_concurrent_target_creation_is_not_overwritten(populated, tmp_path, monkeypatch):
    db, _, _, _ = populated
    target = tmp_path / "race.db"
    real_publish = recovery.publish_new_file

    def concurrent_publish(staged, output):
        target.write_bytes(b"another process created this")
        real_publish(staged, output)

    monkeypatch.setattr(recovery, "publish_new_file", concurrent_publish)
    with pytest.raises(FileExistsError):
        recovery.snapshot_database(db.path, target)
    assert target.read_bytes() == b"another process created this"
    assert list(tmp_path.glob(".commerce-snapshot-*")) == []


@pytest.mark.parametrize("damage", ["stock", "total", "foreign_key"])
def test_bad_business_or_foreign_key_data_is_not_published(populated, tmp_path, damage):
    db, _, _, _ = populated
    with db.connection() as c:
        if damage == "stock":
            c.execute("UPDATE inventory SET available=99")
        elif damage == "total":
            c.execute("UPDATE orders SET total_cents=1")
        else:
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("INSERT INTO inventory VALUES(999999,0)")
    with pytest.raises(recovery.VerificationError):
        recovery.snapshot_database(db.path, tmp_path / "invalid.db")
    assert not (tmp_path / "invalid.db").exists()
    assert list(tmp_path.glob(".commerce-snapshot-*")) == []


@pytest.mark.parametrize("version", [0, 1, 3])
def test_unsupported_version_is_never_migrated(populated, tmp_path, version):
    db, _, _, _ = populated
    with db.connection() as c:
        c.execute(f"PRAGMA user_version={version}")
    with pytest.raises(recovery.VerificationError):
        recovery.snapshot_database(db.path, tmp_path / "wrong-version.db")
    with db.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == version
    assert not (tmp_path / "wrong-version.db").exists()


def test_corrupt_file_is_rejected_without_partial_output(tmp_path):
    source = tmp_path / "corrupt.db"
    source.write_bytes(b"this is not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        recovery.snapshot_database(source, tmp_path / "bad.db")
    assert not (tmp_path / "bad.db").exists()
    assert list(tmp_path.glob(".commerce-snapshot-*")) == []


def test_read_only_connection_rejects_writes_and_initialization(populated):
    db, _, _, _ = populated
    readonly = Database(db.path, read_only=True)
    with readonly.connection() as c:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("UPDATE inventory SET available=99")
    with pytest.raises(RuntimeError):
        readonly.initialize()


def test_backup_timeout_does_not_publish_partial_output(populated, tmp_path, monkeypatch):
    db, _, _, _ = populated
    moments = iter([0.0, 100.0])
    monkeypatch.setattr(recovery.time, "monotonic", lambda: next(moments))
    with pytest.raises(TimeoutError):
        recovery.snapshot_database(db.path, tmp_path / "timeout.db", timeout=1)
    assert not (tmp_path / "timeout.db").exists()
    assert list(tmp_path.glob(".commerce-snapshot-*")) == []


def test_cli_success_and_redacted_failure(populated, tmp_path, capsys):
    db, _, _, _ = populated
    target = tmp_path / "cli.db"
    assert recovery.main(["backup", "--source", db.path, "--output", str(target)]) == 0
    assert '"ok": true' in capsys.readouterr().out
    assert recovery.main(["verify", "--database", str(target)]) == 0
    assert '"verified": true' in capsys.readouterr().out
    assert recovery.main(["restore", "--source", str(target), "--output", str(target)]) == 1
    output = capsys.readouterr().out
    assert '"error": "FileExistsError"' in output
    assert str(tmp_path) not in output
