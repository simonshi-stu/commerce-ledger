"""A v2 marker and balanced rows do not prove recovery preserves future guards."""
import json
import sqlite3

import pytest

from app import recovery, service
from app.db import Database, MIGRATIONS
from app.schemas import Adjustment, ProductCreate


GUARDS = (
    "order_status_transition_guard",
    "order_items_update_guard",
    "order_items_delete_guard",
    "terminal_order_items_insert_guard",
    "stock_movements_update_guard",
    "stock_movements_delete_guard",
)


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "source.db")
    db.initialize()
    product = service.create_product(db, ProductCreate(sku="SCHEMA-001", name="synthetic", price_cents=100))
    service.adjust_stock(db, product["id"], Adjustment(
        delta=5, request_key="schema-stock-001", reason="synthetic"))
    return db


def assert_refused_without_changes(db, tmp_path):
    with db.connection() as connection:
        original = list(connection.iterdump())
    assert service.check_integrity(db, 20)["healthy"]
    with pytest.raises(recovery.VerificationError, match="schema"):
        recovery.verify_database(db.path)
    for operation in ("backup", "restore"):
        target = tmp_path / f"{operation}.db"
        with pytest.raises(recovery.VerificationError, match="schema"):
            recovery.snapshot_database(db.path, target)
        assert not target.exists()
        assert not list(tmp_path.glob(".commerce-snapshot-*"))
    with db.connection() as connection:
        assert list(connection.iterdump()) == original


@pytest.mark.parametrize("guard", GUARDS)
def test_each_missing_history_guard_blocks_verification_and_publication(database, tmp_path, guard):
    with database.connection() as connection:
        connection.execute(f"DROP TRIGGER {guard}")
    assert_refused_without_changes(database, tmp_path)


@pytest.mark.parametrize("guard", GUARDS)
def test_same_named_noop_guard_does_not_satisfy_schema_contract(database, tmp_path, guard):
    with database.connection() as connection:
        table = connection.execute("SELECT tbl_name FROM sqlite_schema WHERE name=?", (guard,)).fetchone()[0]
        connection.execute(f"DROP TRIGGER {guard}")
        connection.execute(f"CREATE TRIGGER {guard} BEFORE UPDATE ON {table} BEGIN SELECT 1; END")
    assert_refused_without_changes(database, tmp_path)


@pytest.mark.parametrize("old,new", [
    ("CHECK(typeof(available)='integer' AND available BETWEEN 0 AND 1000000000)", ""),
    ("REFERENCES products(id)", ""),
    ("request_key TEXT NOT NULL UNIQUE", "request_key TEXT NOT NULL"),
    ("PRIMARY KEY(order_id,product_id)", "UNIQUE(order_id,product_id)"),
])
def test_empty_balanced_database_with_weakened_table_constraints_is_rejected(tmp_path, old, new):
    db = Database(tmp_path / "weak.db")
    with db.connection() as connection:
        for _, migration in MIGRATIONS:
            source = migration.read_text(encoding="utf-8")
            connection.executescript(source.replace(old, new))
        connection.execute("PRAGMA user_version=2")
    assert_refused_without_changes(db, tmp_path)


@pytest.mark.parametrize("extra_ddl", [
    "CREATE TABLE extra_business_data (id INTEGER)",
    "CREATE VIEW extra_inventory AS SELECT * FROM inventory",
    "CREATE INDEX extra_stock_index ON stock_movements(delta)",
    "CREATE TRIGGER extra_trigger AFTER INSERT ON products BEGIN SELECT 1; END",
    "CREATE TABLE sqliteXlookalike (id INTEGER)",
])
def test_unrecognized_application_schema_is_rejected(database, tmp_path, extra_ddl):
    with database.connection() as connection:
        connection.execute(extra_ddl)
    assert_refused_without_changes(database, tmp_path)


def test_missing_required_index_is_rejected(database, tmp_path):
    with database.connection() as connection:
        connection.execute("DROP INDEX idx_orders_created")
    assert_refused_without_changes(database, tmp_path)


def test_sql_literal_changes_are_not_erased_by_whitespace_normalization(database, tmp_path):
    with database.connection() as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='stock_movements_delete_guard'").fetchone()[0]
        connection.execute("DROP TRIGGER stock_movements_delete_guard")
        connection.execute(sql.replace("append-only", "append- only"))
    assert_refused_without_changes(database, tmp_path)


def test_valid_analyzed_database_and_restored_guard_continue_to_work(database, tmp_path):
    with database.connection() as connection:
        connection.execute("ANALYZE")
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_stat1'").fetchone()
    assert recovery.verify_database(database.path)["verified"]
    target = tmp_path / "valid.db"
    assert recovery.snapshot_database(database.path, target)["verified"]
    with Database(target).connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM stock_movements")


def test_real_v1_to_v2_migration_matches_reference_contract(tmp_path):
    db = Database(tmp_path / "migrated.db")
    with db.connection() as connection:
        connection.executescript(MIGRATIONS[0][1].read_text(encoding="utf-8"))
        connection.execute("PRAGMA user_version=1")
    db.initialize()
    assert recovery.verify_database(db.path)["verified"]
    assert recovery.snapshot_database(db.path, tmp_path / "upgraded.db")["verified"]


@pytest.mark.parametrize("operation", ["verify", "backup", "restore"])
def test_cli_refuses_drift_and_redacts_schema_and_paths(database, tmp_path, capsys, operation):
    with database.connection() as connection:
        connection.execute("DROP TRIGGER stock_movements_delete_guard")
    argv = [operation, "--database", database.path] if operation == "verify" else [
        operation, "--source", database.path, "--output", str(tmp_path / "cli.db")]
    assert recovery.main(argv) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"ok": False, "error": "VerificationError"}
    assert str(tmp_path) not in output
    assert "stock_movements" not in output
    assert not (tmp_path / "cli.db").exists()
