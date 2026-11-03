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


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path / "test.db", KEY)
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        conn.execute("PRAGMA user_version=2")
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

