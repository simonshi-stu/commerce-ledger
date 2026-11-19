"""Shared API-boundary and whole-order transaction regressions.

The PostgreSQL suite imports these same tests against its real-engine fixture.
No mocked engine is used to claim PostgreSQL correctness.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app import service
from app.schemas import MAX_DB_INTEGER, OrderCreate
from test_backend import api, product, order


@pytest.mark.parametrize("value", [0, -1, MAX_DB_INTEGER + 1, -(1 << 63) - 1, 10**100])
@pytest.mark.parametrize("operation", ["get", "put", "delete", "adjust", "movements"])
def test_product_path_ids_fit_database_before_service(api, monkeypatch, value, operation):
    client, db = api

    def unexpected_database_use(*args, **kwargs):
        pytest.fail("invalid product ID reached the database")

    monkeypatch.setattr(db, "connection", unexpected_database_use)
    monkeypatch.setattr(db, "transaction", unexpected_database_use)
    path = f"/products/{value}"
    if operation == "put":
        response = client.put(path, json={"name": "模拟改名", "price_cents": 10})
    elif operation == "delete":
        response = client.delete(path)
    elif operation == "adjust":
        response = client.post(path + "/stock-adjustments", json={
            "delta": 1, "request_key": "bounded-stock-001", "reason": "模拟入库"})
    elif operation == "movements":
        response = client.get(path + "/movements")
    else:
        response = client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("value", [MAX_DB_INTEGER + 1, 10**100])
def test_order_product_id_overflow_is_validation_error(api, monkeypatch, value):
    client, db = api

    def unexpected_transaction(*args, **kwargs):
        pytest.fail("invalid order product ID opened a transaction")

    monkeypatch.setattr(db, "transaction", unexpected_transaction)
    response = client.post("/orders", json=order(value, "bounded-order-001"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("endpoint", ["/products", "/orders", "/products/1/movements"])
@pytest.mark.parametrize("value", [MAX_DB_INTEGER + 1, 10**100])
def test_pagination_offset_overflow_is_validation_error(api, endpoint, value):
    client, _ = api
    response = client.get(endpoint, params={"offset": value})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_database_integer_upper_boundary_remains_valid(api):
    client, _ = api
    spec = client.get("/openapi.json").json()
    # Some OpenAPI tooling converts numeric bounds to floating point. Preserve
    # the exact bound as a string extension as well as runtime int validation.
    assert spec["components"]["schemas"]["OrderItem"]["properties"]["product_id"]["x-maximum-integer"] == str(MAX_DB_INTEGER)
    for definition in spec["paths"].values():
        for operation in definition.values():
            for parameter in operation.get("parameters", []):
                if parameter["name"] in ("product_id", "offset"):
                    assert parameter["schema"]["x-maximum-integer"] == str(MAX_DB_INTEGER)
    # Largest representable, absent ID is a normal not-found, not overflow.
    assert client.get(f"/products/{MAX_DB_INTEGER}").status_code == 404
    assert client.post("/orders", json=order(MAX_DB_INTEGER)).status_code == 404
    assert client.get("/products", params={"offset": MAX_DB_INTEGER}).json() == []
    assert client.get("/orders", params={"offset": MAX_DB_INTEGER}).json() == []


def test_multi_product_reverse_order_race_is_atomic(api):
    client, db = api
    first, second = product(client, "RACE-A", 1), product(client, "RACE-B", 1)
    barrier = threading.Barrier(10)

    def buy(index):
        ids = [first, second] if index % 2 else [second, first]
        payload = OrderCreate(request_key=f"reverse-race-{index:03}", items=[
            {"product_id": pid, "quantity": 1} for pid in ids])
        barrier.wait(timeout=10)
        try:
            service.place_order(db, payload)
            return 201
        except service.BusinessError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(buy, range(10)))
    assert results.count(201) == 1
    assert results.count(409) == 9
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM stock_movements WHERE kind='SALE'").fetchone()[0] == 2
    assert client.get(f"/products/{first}").json()["available"] == 0
    assert client.get(f"/products/{second}").json()["available"] == 0
    assert client.get("/integrity").json()["healthy"]


def test_cancel_overflow_rolls_back_every_refund_then_can_retry(api):
    client, db = api
    first, second = product(client, "REFUND-A", 2), product(client, "REFUND-B", 1)
    response = client.post("/orders", json={"request_key": "refund-overflow-001", "items": [
        {"product_id": first, "quantity": 1}, {"product_id": second, "quantity": 1}]})
    assert response.status_code == 201
    oid = response.json()["id"]
    assert client.post(f"/products/{second}/stock-adjustments", json={
        "delta": 1000000000, "request_key": "fill-capacity-001", "reason": "模拟补货"}).status_code == 201
    failed = client.post(f"/orders/{oid}/cancel")
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "INVALID_STOCK"
    assert client.get(f"/orders/{oid}").json()["status"] == "PLACED"
    assert client.get(f"/products/{first}").json()["available"] == 1
    assert client.get(f"/products/{second}").json()["available"] == 1000000000
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM stock_movements WHERE kind='CANCEL'").fetchone()[0] == 0
    assert client.get("/integrity").json()["healthy"]
    assert client.post(f"/products/{second}/stock-adjustments", json={
        "delta": -1, "request_key": "make-capacity-001", "reason": "模拟调出"}).status_code == 201
    assert client.post(f"/orders/{oid}/cancel").json()["status"] == "CANCELLED"
    assert client.post(f"/orders/{oid}/cancel").json()["status"] == "CANCELLED"
    assert client.get(f"/products/{first}").json()["available"] == 2
    assert client.get(f"/products/{second}").json()["available"] == 1000000000
    with db.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM stock_movements WHERE kind='CANCEL'").fetchone()[0] == 2
    assert client.get("/integrity").json()["healthy"]
