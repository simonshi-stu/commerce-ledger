from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient

from app import reservations, service
from app.main import create_app
from app.schemas import ReservationCreate

MANAGER = "synthetic-manager"
READER = "synthetic-reader"


@pytest.fixture
def api(tmp_path, monkeypatch):
    clock = [100]
    monkeypatch.setattr(reservations, "now_epoch", lambda: clock[0])
    app = create_app(tmp_path / "reservations.db", MANAGER, READER)
    with TestClient(app, headers={"X-API-Key": MANAGER}) as client:
        yield client, app.state.db, clock


def stock(c, quantity):
    response = c.post("/products", json={"sku": "DEMO-SKU", "name": "Synthetic", "price_cents": 2000})
    assert response.status_code == 201
    pid = response.json()["id"]
    response = c.post(f"/products/{pid}/stock-adjustments", json={
        "request_key": "seed-stock", "delta": quantity, "reason": "Synthetic"
    })
    assert response.status_code == 201
    return pid


def hold_body(pid, key="hold-demo", quantity=2):
    return {"request_key": key, "product_id": pid, "quantity": quantity, "ttl_seconds": 10}


def reserve(c, pid, **kwargs):
    response = c.post("/reservations", json=hold_body(pid, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def receipt(c, rid, event="event-demo", amount=4000):
    return c.post(f"/reservations/{rid}/simulated-payment", json={"event_id": event, "amount_cents": amount})


def available(c, pid):
    return c.get(f"/products/{pid}").json()["available"]


def test_hold_promotes_once_at_snapshotted_price_and_existing_cancellation_works(api):
    c, _, _ = api
    pid = stock(c, 3)
    held = reserve(c, pid)
    rid = held["id"]
    assert held["state"] == "HELD" and available(c, pid) == 1
    assert reserve(c, pid) == held
    assert c.post("/reservations", json=hold_body(pid, quantity=1)).status_code == 409
    assert c.get(f"/orders/{rid}").status_code == 404
    assert receipt(c, rid, amount=3999).status_code == 409
    assert c.put(f"/products/{pid}", json={"name": "Updated", "price_cents": 2500}).status_code == 200
    paid = receipt(c, rid).json()
    assert paid["outcome"] == "PAID" and paid["order_id"] == rid
    assert receipt(c, rid).json() == paid
    assert receipt(c, rid, amount=4001).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert receipt(c, rid, event="new-event").json()["outcome"] == "ALREADY_PAID"
    assert c.get(f"/reservations/{rid}").json()["state"] == "PAID"
    assert c.get(f"/orders/{rid}").json()["total_cents"] == 4000
    assert available(c, pid) == 1
    assert c.get("/integrity").json()["healthy"]
    assert c.post(f"/orders/{rid}/cancel").status_code == 200
    assert c.post(f"/orders/{rid}/cancel").status_code == 200
    assert available(c, pid) == 3
    assert receipt(c, rid).json() == paid
    assert c.get("/integrity").json()["healthy"]


@pytest.mark.parametrize("sweep_first", [False, True])
def test_callback_at_expiry_never_revives_hold(api, sweep_first):
    c, _, clock = api
    pid = stock(c, 2)
    rid = reserve(c, pid)["id"]
    clock[0] = 109
    assert c.post("/reservations/expire").json() == {"released": 0}
    clock[0] = 110
    if sweep_first:
        assert c.post("/reservations/expire").json() == {"released": 1}
    late = receipt(c, rid).json()
    assert late["outcome"] == "LATE_PAYMENT" and late["order_id"] is None
    assert receipt(c, rid).json() == late
    assert c.get(f"/reservations/{rid}").json()["state"] == "EXPIRED"
    assert c.get("/orders").json() == []
    assert c.post("/reservations/expire").json() == {"released": 0}
    assert available(c, pid) == 2
    assert c.get("/integrity").json()["healthy"]
    movements = c.get(f"/products/{pid}/movements").json()
    assert [m["delta"] for m in movements] == [2, -2, 2]


def test_adjustments_leave_headroom_for_expiry(api):
    c, _, clock = api
    pid = stock(c, 1_000_000_000)
    reserve(c, pid)
    r = c.post(f"/products/{pid}/stock-adjustments", json={
        "request_key": "extra-stock", "delta": 1, "reason": "Synthetic"
    })
    assert r.status_code == 409
    clock[0] = 110
    assert c.post("/reservations/expire").json() == {"released": 1}
    assert available(c, pid) == 1_000_000_000
    assert c.get("/integrity").json()["healthy"]


def test_reader_can_read_but_not_change_lifecycle(api):
    c, _, _ = api
    pid = stock(c, 2)
    rid = reserve(c, pid)["id"]
    headers = {"X-API-Key": READER}
    assert c.get(f"/reservations/{rid}", headers=headers).status_code == 200
    assert c.post("/reservations", json=hold_body(pid), headers=headers).status_code == 403
    assert c.post("/reservations/expire", headers=headers).status_code == 403
    assert c.post(f"/reservations/{rid}/simulated-payment", headers=headers,
                  json={"event_id": "read-only", "amount_cents": 4000}).status_code == 403


@pytest.mark.parametrize("buyers", [50, 100])
def test_concurrent_reservations_never_oversell(api, buyers):
    c, db, clock = api
    pid = stock(c, 20)
    start = threading.Barrier(buyers)

    def submit(i):
        data = ReservationCreate(**hold_body(pid, key=f"buyer-{i:03}", quantity=1))
        start.wait(timeout=10)
        try:
            return reservations.reserve(db, data)["state"]
        except service.BusinessError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=buyers) as pool:
        outcomes = list(pool.map(submit, range(buyers)))
    assert outcomes.count("HELD") == 20
    assert outcomes.count("INSUFFICIENT_STOCK") == buyers - 20
    assert available(c, pid) == 0
    assert c.get("/integrity").json()["healthy"]
    clock[0] = 110
    assert c.post("/reservations/expire").json() == {"released": 20}
    assert available(c, pid) == 20
    assert c.get("/integrity").json()["healthy"]


def test_simultaneous_repeated_payment_event_creates_one_order(api):
    c, db, _ = api
    pid = stock(c, 4)
    rid = reserve(c, pid)["id"]
    start = threading.Barrier(8)

    def submit(_):
        start.wait(timeout=10)
        result = receipt(c, rid)
        assert result.status_code == 200
        return result.json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert all(row == results[0] for row in results)
    assert results[0]["outcome"] == "PAID"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM simulated_payment_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM stock_movements WHERE kind='SALE'").fetchone()[0] == 1
    assert available(c, pid) == 2
    assert c.get("/integrity").json()["healthy"]
