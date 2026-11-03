from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Database
from app.seed import seed
from app.schemas import OrderCreate
from app import service

KEY = "local-test-key"
READ_KEY = "local-test-read-key"


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path / "test.db", KEY, READ_KEY)
    with TestClient(app, headers={"X-API-Key": KEY}) as client:
        yield client, app.state.db


def product(client, sku="TEST-001", stock=5):
    response = client.post("/products", json={"sku": sku, "name": "模拟商品", "price_cents": 2990})
    assert response.status_code == 201
    pid = response.json()["id"]
    if stock:
        assert client.post(f"/products/{pid}/stock-adjustments",
                           json={"delta": stock, "request_key": f"initial-{sku}", "reason": "模拟入库"}).status_code == 201
    return pid


def order(pid, key="order-0001", quantity=1):
    return {"request_key": key, "items": [{"product_id": pid, "quantity": quantity}]}


def test_complete_business_flow_and_snapshot(api):
    c, db = api
    pid = product(c)
    result = c.post("/orders", json=order(pid, quantity=2))
    assert result.status_code == 201
    data = result.json()
    assert data["total_cents"] == 5980
    assert c.get(f"/products/{pid}").json()["available"] == 3
    assert c.put(f"/products/{pid}", json={"name": "新名称", "price_cents": 5000}).status_code == 200
    assert c.get("/orders/" + data["id"]).json()["items"][0]["unit_price_cents"] == 2990
    assert c.post("/orders/" + data["id"] + "/fulfill").json()["status"] == "FULFILLED"
    assert c.post("/orders/" + data["id"] + "/cancel").status_code == 409


def test_cancel_exactly_once(api):
    c, db = api
    pid = product(c)
    oid = c.post("/orders", json=order(pid)).json()["id"]
    for _ in range(2):
        assert c.post(f"/orders/{oid}/cancel").status_code == 200
    assert c.get(f"/products/{pid}").json()["available"] == 5
    movements = c.get(f"/products/{pid}/movements").json()
    assert sum(m["delta"] for m in movements) == 5
    assert len([m for m in movements if m["kind"] == "CANCEL"]) == 1
    assert c.post("/orders", json=order(pid)).json()["status"] == "CANCELLED"


def test_multi_item_failure_rolls_back_everything(api):
    c, db = api
    first, second = product(c, "FIRST"), product(c, "SECOND", 0)
    payload = {"request_key": "rollback-0001", "items": [
        {"product_id": first, "quantity": 2}, {"product_id": second, "quantity": 1}]}
    assert c.post("/orders", json=payload).status_code == 409
    assert c.get(f"/products/{first}").json()["available"] == 5
    assert c.get("/orders").json() == []
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM order_items").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM stock_movements WHERE kind='SALE'").fetchone()[0] == 0


def test_order_idempotency_and_conflict(api):
    c, db = api
    pid = product(c)
    first = c.post("/orders", json=order(pid)).json()
    assert c.post("/orders", json=order(pid)).json() == first
    assert c.post("/orders", json=order(pid, quantity=2)).status_code == 409
    assert c.get(f"/products/{pid}").json()["available"] == 4


def test_adjustment_idempotency_and_underflow(api):
    c, db = api
    pid = product(c)
    path = f"/products/{pid}/stock-adjustments"
    payload = {"delta": 3, "request_key": "adjust-0001", "reason": "模拟补货"}
    first = c.post(path, json=payload).json()
    assert c.post(path, json=payload).json() == first
    assert c.post(path, json={**payload, "delta": 4}).status_code == 409
    assert c.post(path, json={**payload, "request_key": "adjust-0002", "delta": -9}).status_code == 409
    assert c.get(f"/products/{pid}").json()["available"] == 8


def test_soft_delete_blocks_new_sales_preserves_history(api):
    c, db = api
    pid = product(c)
    oid = c.post("/orders", json=order(pid)).json()["id"]
    assert c.delete(f"/products/{pid}").json()["active"] == 0
    assert c.post("/orders", json=order(pid, "order-0002")).status_code == 409
    assert c.get(f"/orders/{oid}").status_code == 200
    assert c.post(f"/orders/{oid}/cancel").status_code == 200


@pytest.mark.parametrize("field,value", [("quantity", 0), ("quantity", -1), ("quantity", 1.5), ("quantity", True), ("quantity", "1"), ("product_id", 0)])
def test_invalid_order_fields(api, field, value):
    c, db = api
    pid = product(c)
    payload = order(pid)
    payload["items"][0][field] = value
    assert c.post("/orders", json=payload).status_code == 422


def test_duplicate_items_empty_items_and_unknown_fields(api):
    c, db = api
    pid = product(c)
    payload = order(pid)
    payload["items"] *= 2
    assert c.post("/orders", json=payload).status_code == 422
    assert c.post("/orders", json={"request_key": "empty-0001", "items": []}).status_code == 422
    assert c.post("/orders", json={**order(pid), "total_cents": 1}).status_code == 422
    assert c.get("/products?limit=101").status_code == 422


def test_auth_not_found_unique_sku_and_sql_text(api):
    c, db = api
    assert c.get("/products", headers={"X-API-Key": "wrong"}).status_code == 401
    pid = product(c)
    assert c.get("/products/99999").status_code == 404
    assert c.get("/orders/missing").status_code == 404
    assert c.post("/products", json={"sku": "TEST-001", "name": "重复", "price_cents": 10}).status_code == 409
    text = "x'; DROP TABLE products; --"
    assert c.put(f"/products/{pid}", json={"name": text, "price_cents": 10}).json()["name"] == text
    assert len(c.get("/products").json()) == 1


def test_database_constraints_and_foreign_keys(api):
    c, db = api
    pid = product(c)
    with db.connection() as conn:
        for sql in [
            "UPDATE inventory SET available=-1",
            "INSERT INTO inventory VALUES(999999,0)",
            "UPDATE products SET price_cents=0",
            "UPDATE products SET price_cents=1.5",
            "UPDATE stock_movements SET delta=1.5",
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_seed_is_repeatable(tmp_path):
    db = Database(tmp_path / "seed.db")
    seed(db)
    with db.transaction() as conn:
        conn.execute("UPDATE products SET price_cents=123 WHERE id=1")
    seed(db)
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 5
        assert conn.execute("SELECT price_cents FROM products WHERE id=1").fetchone()[0] == 123
        assert conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0] == 5


def test_twenty_connections_compete_for_last_unit(api):
    c, db = api
    pid = product(c, stock=1)
    barrier = threading.Barrier(20)

    def buy(i):
        barrier.wait(timeout=10)
        try:
            service.place_order(db, OrderCreate(**order(pid, f"concurrent-{i:03}")))
            return 201
        except service.BusinessError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(buy, range(20)))
    assert statuses.count(201) == 1
    assert statuses.count(409) == 19
    assert c.get(f"/products/{pid}").json()["available"] == 0
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 1
        assert conn.execute("SELECT sum(delta) FROM stock_movements WHERE product_id=?", (pid,)).fetchone()[0] == 0


def test_concurrent_retries_create_one_order(api):
    c, db = api
    pid = product(c, stock=5)
    barrier = threading.Barrier(10)

    def buy(_):
        barrier.wait(timeout=10)
        return service.place_order(db, OrderCreate(**order(pid)))["id"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(buy, range(10)))
    assert len(set(ids)) == 1
    assert c.get(f"/products/{pid}").json()["available"] == 4


def test_concurrent_cancel_returns_stock_once(api):
    c, db = api
    pid = product(c)
    oid = c.post("/orders", json=order(pid)).json()["id"]
    barrier = threading.Barrier(10)

    def cancel(_):
        barrier.wait(timeout=10)
        return service.transition_order(db, oid, "CANCELLED")["status"]

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert list(pool.map(cancel, range(10))) == ["CANCELLED"] * 10
    assert c.get(f"/products/{pid}").json()["available"] == 5


def test_order_item_order_does_not_change_idempotency(api):
    c, db = api
    a, b = product(c, "SORT-A"), product(c, "SORT-B")
    payload = {"request_key": "sorted-0001", "items": [
        {"product_id": a, "quantity": 1}, {"product_id": b, "quantity": 2}]}
    first = c.post("/orders", json=payload).json()
    payload["items"].reverse()
    assert c.post("/orders", json=payload).json() == first


def test_database_busy_returns_retryable_response(api, monkeypatch):
    c, db = api
    pid = product(c)
    original = db.connect

    def short_wait():
        conn = original()
        conn.execute("PRAGMA busy_timeout=1")
        return conn

    monkeypatch.setattr(db, "connect", short_wait)
    with db.transaction():
        response = c.post("/orders", json=order(pid))
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["code"] == "DATABASE_BUSY"
    assert c.get("/orders").json() == []


def test_initial_schema_migration_and_future_version_guard(tmp_path):
    db = Database(tmp_path / "migration.db")
    db.initialize()
    db.initialize()
    with db.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        conn.execute("PRAGMA user_version=3")
    with pytest.raises(RuntimeError):
        db.initialize()


def test_twenty_http_buyers_compete_for_last_unit(api):
    client, db = api
    pid = product(client, stock=1)
    barrier = threading.Barrier(20)

    def buy(i):
        barrier.wait(timeout=10)
        return client.post("/orders", json=order(pid, f"http-race-{i:03}"))

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(buy, range(20)))
    assert sum(r.status_code == 201 for r in responses) == 1
    rejected = [r for r in responses if r.status_code != 201]
    assert len(rejected) == 19
    assert all(r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_STOCK" for r in rejected)
    assert client.get(f"/products/{pid}").json()["available"] == 0
    assert len(client.get("/orders").json()) == 1


def test_cancel_and_fulfill_race_has_one_terminal_state(api):
    client, db = api
    pid = product(client, stock=1)
    oid = client.post("/orders", json=order(pid)).json()["id"]
    barrier = threading.Barrier(2)

    def transition(target):
        barrier.wait(timeout=10)
        try:
            return service.transition_order(db, oid, target)["status"]
        except service.BusinessError as exc:
            assert exc.code == "INVALID_ORDER_STATE"
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(transition, ["CANCELLED", "FULFILLED"]))
    assert results.count("CONFLICT") == 1
    final = client.get(f"/orders/{oid}").json()["status"]
    stock = client.get(f"/products/{pid}").json()["available"]
    assert (final, stock) in [("CANCELLED", 1), ("FULFILLED", 0)]


def test_failed_order_can_retry_same_key_after_restock(api):
    client, db = api
    pid = product(client, stock=0)
    payload = order(pid)
    assert client.post("/orders", json=payload).status_code == 409
    assert client.post(f"/products/{pid}/stock-adjustments", json={
        "delta": 1, "request_key": "retry-restock-001", "reason": "模拟补货"}).status_code == 201
    created = client.post("/orders", json=payload)
    assert created.status_code == 201
    assert client.post("/orders", json=payload).json()["id"] == created.json()["id"]


def test_openapi_documents_authentication_and_response_contracts(api):
    client, db = api
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key"}
    operation = spec["paths"]["/orders"]["post"]
    assert operation["security"] == [{"APIKeyHeader": []}]
    assert operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/OrderRead")
    assert "unit_price_cents" in spec["components"]["schemas"]["OrderItemRead"]["properties"]
    with TestClient(create_app(db.path, KEY)) as unauthenticated:
        assert unauthenticated.get("/products").status_code == 401


def test_integrity_is_healthy_after_business_flow(api):
    client, db = api
    a, b = product(client, "AUDIT-A"), product(client, "AUDIT-B")
    oid = client.post("/orders", json=order(a)).json()["id"]
    assert client.post(f"/orders/{oid}/cancel").status_code == 200
    client.post("/orders", json=order(b, "audit-order-002"))
    response = client.get("/integrity")
    assert response.status_code == 200
    result = response.json()
    assert result["healthy"] and not result["truncated"]
    assert result["products_checked"] == 2 and result["orders_checked"] == 2
    assert result["stock_mismatches"] == result["order_mismatches"] == []
    assert client.get("/integrity", headers={"X-API-Key": "wrong"}).status_code == 401


def test_integrity_detects_corruption_and_bounds_details(api):
    client, db = api
    a, b = product(client, "BROKEN-A"), product(client, "BROKEN-B")
    oid = client.post("/orders", json=order(a)).json()["id"]
    # 仅在测试临时库中模拟人工绕过业务层，API 不提供这些破坏操作。
    with db.transaction() as conn:
        conn.execute("UPDATE inventory SET available=99 WHERE product_id=?", (a,))
        conn.execute("DELETE FROM inventory WHERE product_id=?", (b,))
        conn.execute("UPDATE orders SET total_cents=0 WHERE id=?", (oid,))
    result = client.get("/integrity?limit=1").json()
    assert not result["healthy"] and result["truncated"]
    assert result["stock_mismatch_count"] == 2
    assert result["order_mismatch_count"] == 1
    assert len(result["stock_mismatches"]) == 1
    assert result["stock_mismatches"][0]["available"] == 99
    assert result["stock_mismatches"][0]["ledger_available"] == 4
    assert result["order_mismatches"][0]["item_total_cents"] == 2990
    assert client.get("/integrity?limit=101").status_code == 422
    with db.connection() as conn:
        assert conn.execute("SELECT available FROM inventory WHERE product_id=?", (a,)).fetchone()[0] == 99
    missing = client.get("/integrity?limit=100").json()["stock_mismatches"][1]
    assert missing["product_id"] == b and missing["available"] is None


def test_read_snapshot_does_not_mix_committed_versions(api):
    client, db = api
    pid = product(client)
    from app.schemas import Adjustment
    with db.read_snapshot() as conn:
        before = conn.execute("SELECT available FROM inventory WHERE product_id=?", (pid,)).fetchone()[0]
        service.adjust_stock(db, pid, Adjustment(delta=2, request_key="snapshot-adjust-001", reason="模拟补货"))
        ledger = conn.execute("SELECT SUM(delta) FROM stock_movements WHERE product_id=?", (pid,)).fetchone()[0]
        assert before == ledger == 5
    assert client.get(f"/products/{pid}").json()["available"] == 7
    assert client.get("/integrity").json()["healthy"]


def test_read_snapshot_connection_refuses_writes(api):
    client, db = api
    product(client)
    with db.read_snapshot() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE inventory SET available=999")
    assert client.get("/integrity").json()["healthy"]


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/products", {"sku": "NO-WRITE", "name": "blocked", "price_cents": 100}),
    ("PUT", "/products/1", {"name": "blocked", "price_cents": 100}),
    ("DELETE", "/products/1", None),
    ("POST", "/products/1/stock-adjustments", {"delta": 1, "request_key": "blocked-adjust", "reason": "blocked"}),
    ("POST", "/orders", {"request_key": "blocked-order", "items": [{"product_id": 1, "quantity": 1}]}),
    ("POST", "/orders/{oid}/cancel", None),
    ("POST", "/orders/{oid}/fulfill", None),
])
def test_reader_cannot_modify_any_business_surface(api, method, path, body):
    client, db = api
    pid = product(client)
    oid = client.post("/orders", json=order(pid)).json()["id"]
    response = client.request(method, path.format(oid=oid), json=body, headers={"X-API-Key": READ_KEY})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert client.get(f"/products/{pid}").json()["available"] == 4
    assert client.get(f"/orders/{oid}").json()["status"] == "PLACED"
    assert len(client.get("/products").json()) == 1


def test_reader_can_read_but_is_not_a_separate_customer(api):
    client, db = api
    pid = product(client)
    oid = client.post("/orders", json=order(pid)).json()["id"]
    for path in ["/health", "/products", f"/products/{pid}", f"/products/{pid}/movements",
                 "/orders", f"/orders/{oid}", "/integrity"]:
        response = client.get(path, headers={"X-API-Key": READ_KEY})
        assert response.status_code == 200
        assert len(response.headers["X-Request-ID"]) == 32


def test_key_configuration_fails_closed_and_reader_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("COMMERCE_API_KEY", raising=False)
    monkeypatch.delenv("COMMERCE_READ_API_KEY", raising=False)
    for key in [None, "", "   "]:
        with pytest.raises(RuntimeError):
            create_app(tmp_path / "invalid.db", key)
    with pytest.raises(RuntimeError):
        create_app(tmp_path / "invalid.db", KEY, KEY)
    with TestClient(create_app(tmp_path / "manager.db", KEY, "")) as client:
        assert client.get("/products", headers={"X-API-Key": READ_KEY}).status_code == 401
        assert client.get("/products", headers={"X-API-Key": KEY}).status_code == 200


def test_unexpected_failure_rolls_back_before_commit_and_logs_no_secret(api, monkeypatch, caplog):
    client, db = api
    pid = product(client)
    original = service.order_detail
    marker = "DO-NOT-LOG-REQUEST-OR-KEY"

    def fail_before_return(conn, oid):
        raise RuntimeError(marker)

    monkeypatch.setattr(service, "order_detail", fail_before_return)
    response = client.post("/orders?debug=" + marker, json=order(pid), headers={"X-Request-ID": marker})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert marker not in response.text and response.headers["X-Request-ID"] != marker
    application_logs = "\n".join(r.getMessage() for r in caplog.records if r.name == "app.main")
    assert "route=/orders" in application_logs and "error_type=RuntimeError" in application_logs
    assert marker not in application_logs and KEY not in application_logs
    assert all(r.exc_info is None for r in caplog.records if r.name == "app.main")
    monkeypatch.setattr(service, "order_detail", original)
    assert client.get("/orders").json() == []
    assert client.get(f"/products/{pid}").json()["available"] == 5
    assert client.post("/orders", json=order(pid)).status_code == 201


def test_response_failure_after_commit_retry_does_not_charge_twice(api, monkeypatch):
    client, db = api
    pid = product(client)
    original = service.place_order
    committed = []

    def corrupt_response(database, data):
        result = original(database, data)
        committed.append(result["id"])
        return {**result, "total_cents": "not-an-integer"}

    monkeypatch.setattr(service, "place_order", corrupt_response)
    assert client.post("/orders", json=order(pid)).status_code == 500
    monkeypatch.setattr(service, "place_order", original)
    retry = client.post("/orders", json=order(pid))
    assert retry.status_code == 201 and retry.json()["id"] == committed[0]
    assert client.get(f"/products/{pid}").json()["available"] == 4
    assert len(client.get("/orders").json()) == 1


def test_database_error_message_is_redacted(api, monkeypatch, caplog):
    client, db = api
    marker = "PRIVATE-DATABASE-ERROR-CONTENT"

    def fail(*args):
        raise sqlite3.OperationalError(marker)

    monkeypatch.setattr(service, "list_products", fail)
    response = client.get("/products")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DATABASE_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert marker not in response.text
    assert marker not in "\n".join(r.getMessage() for r in caplog.records if r.name == "app.main")


def make_legacy_v1_database(path):
    """真实创建旧版文件，不借助新版初始化代码伪装升级。"""
    from pathlib import Path
    db = Database(path)
    with db.connection() as conn:
        conn.executescript(Path("app/schema.sql").read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version=1")
    from app.schemas import ProductCreate
    service.create_product(db, ProductCreate(
        sku="LEGACY-001", name="旧版模拟商品", price_cents=2990))
    from app.schemas import Adjustment
    service.adjust_stock(db, 1, Adjustment(delta=5, request_key="legacy-stock-001", reason="旧版模拟入库"))
    created = service.place_order(db, OrderCreate(**order(1, "legacy-order-001", quantity=2)))
    return db, created


def test_migration_v1_to_v2_preserves_existing_business_data(tmp_path):
    db, before = make_legacy_v1_database(tmp_path / "legacy.db")
    with db.connection() as conn:
        inventory_before = tuple(conn.execute("SELECT * FROM inventory").fetchone())
        movements_before = [tuple(row) for row in conn.execute("SELECT * FROM stock_movements ORDER BY id")]
    db.initialize()
    db.initialize()
    with db.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert service.order_detail(conn, before["id"]) == before
        assert tuple(conn.execute("SELECT * FROM inventory").fetchone()) == inventory_before
        assert [tuple(row) for row in conn.execute("SELECT * FROM stock_movements ORDER BY id")] == movements_before
    assert service.check_integrity(db, 20)["healthy"]
    assert service.transition_order(db, before["id"], "CANCELLED")["status"] == "CANCELLED"


def test_failed_migration_rolls_back_ddl_and_keeps_old_version(tmp_path, monkeypatch):
    import app.db as db_module
    db, before = make_legacy_v1_database(tmp_path / "failed-migration.db")
    broken = tmp_path / "broken.sql"
    broken.write_text("CREATE TABLE must_rollback(id INTEGER);\nTHIS IS NOT VALID SQL;", encoding="utf-8")
    monkeypatch.setattr(db_module, "MIGRATIONS", (db_module.MIGRATIONS[0], (2, broken)))
    with pytest.raises(sqlite3.OperationalError):
        db.initialize()
    with db.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone() is None
        assert service.order_detail(conn, before["id"]) == before


@pytest.mark.parametrize("terminal", ["CANCELLED", "FULFILLED"])
def test_database_guards_terminal_state_and_order_history(api, terminal):
    client, db = api
    pid = product(client)
    other_pid = product(client, "OTHER-ITEM")
    oid = client.post("/orders", json=order(pid)).json()["id"]
    service.transition_order(db, oid, terminal)
    with db.connection() as conn:
        for target in ["PLACED", "FULFILLED" if terminal == "CANCELLED" else "CANCELLED"]:
            with pytest.raises(sqlite3.IntegrityError, match="state transition"):
                conn.execute("UPDATE orders SET status=? WHERE id=?", (target, oid))
        for sql, parameters in [
            ("UPDATE order_items SET unit_price_cents=1 WHERE order_id=?", (oid,)),
            ("DELETE FROM order_items WHERE order_id=?", (oid,)),
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, parameters)
        with pytest.raises(sqlite3.IntegrityError, match="terminal order"):
            conn.execute("INSERT INTO order_items VALUES(?,?,1,1)", (oid, other_pid))
    assert client.get(f"/orders/{oid}").json()["status"] == terminal
    assert client.get("/integrity").json()["healthy"]


def test_stock_history_is_append_only_in_database(api):
    client, db = api
    pid = product(client)
    with db.connection() as conn:
        for sql in ["UPDATE stock_movements SET reason='rewritten'", "DELETE FROM stock_movements"]:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(sql)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            oid = client.post("/orders", json=order(pid)).json()["id"]
            conn.execute("UPDATE order_items SET quantity=2 WHERE order_id=?", (oid,))
    assert client.get("/integrity").json()["healthy"]


def test_migration_command_backs_up_old_data_and_is_repeatable(tmp_path):
    from app.migrate import inspect_version, migrate_database
    from pathlib import Path
    db, before = make_legacy_v1_database(tmp_path / "command.db")
    result = migrate_database(db.path)
    assert result["changed"] and inspect_version(db.path) == 2
    assert inspect_version(result["backup"]) == 1
    with Database(result["backup"]).connection() as conn:
        assert service.order_detail(conn, before["id"]) == before
    assert migrate_database(db.path)["backup"] is None
    assert len(list(Path(tmp_path).glob("*.bak"))) == 1


def test_version_check_does_not_create_files_or_overwrite_future_database(tmp_path):
    from app.migrate import inspect_version, migrate_database
    missing = tmp_path / "missing.db"
    assert inspect_version(missing) == 0 and not missing.exists()
    future = tmp_path / "future.db"
    with sqlite3.connect(future) as conn:
        conn.execute("PRAGMA user_version=3")
    with pytest.raises(RuntimeError):
        migrate_database(future)
    assert inspect_version(future) == 3
    assert list(tmp_path.glob("*.bak")) == []


def test_startup_requires_explicit_migration_for_existing_old_database(tmp_path):
    db, before = make_legacy_v1_database(tmp_path / "startup-legacy.db")
    with pytest.raises(RuntimeError, match="app.migrate"):
        with TestClient(create_app(db.path, KEY, "")):
            pass
    from app.migrate import inspect_version
    assert inspect_version(db.path) == 1
    with db.connection() as conn:
        assert service.order_detail(conn, before["id"]) == before

