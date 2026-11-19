"""Synthetic balanced-but-inconsistent history; no guard is disabled.

Imported by the optional real PostgreSQL suite with its own actual-engine fixture.
"""
from contextlib import contextmanager

import pytest

from app import service
from test_backend import api, product, order

CASES = [
    ("missing_sale", 1), ("sale_wrong_sign", 1), ("sale_wrong_quantity", 1),
    ("missing_cancel", 1), ("cancel_wrong_sign", 1), ("cancel_wrong_quantity", 1),
    ("cancel_while_placed", 1), ("cancel_while_fulfilled", 1),
    ("orphan_sale", 1), ("orphan_cancel", 1), ("wrong_product", 2),
]


def inject_balanced_history(client, db, case, suffix="one"):
    pid = product(client, "SEMANTIC-" + suffix.upper(), stock=10)
    oid = "synthetic-order-" + suffix
    other = product(client, "OTHER-" + suffix.upper(), stock=10) if case == "wrong_product" else pid
    with db.transaction() as c:
        if not case.startswith("orphan"):
            c.execute("INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',2990)",
                      (oid, "synthetic-key-" + suffix, "synthetic"))
            c.execute("INSERT INTO order_items VALUES(?,?,1,2990)", (oid, pid))
        if case.startswith("orphan"):
            movements = [(pid, "SALE", -1)] if case == "orphan_sale" else [(pid, "CANCEL", 1)]
        else:
            sale_delta = 1 if case == "sale_wrong_sign" else -2 if case == "sale_wrong_quantity" else -1
            movements = [] if case == "missing_sale" else [(other, "SALE", sale_delta)]
            if case in {"missing_cancel", "cancel_wrong_sign", "cancel_wrong_quantity"}:
                c.execute("UPDATE orders SET status='CANCELLED' WHERE id=?", (oid,))
            if case in {"cancel_wrong_sign", "cancel_wrong_quantity", "cancel_while_placed", "cancel_while_fulfilled"}:
                movements.append((pid, "CANCEL", -1 if case == "cancel_wrong_sign" else 2 if case == "cancel_wrong_quantity" else 1))
            if case == "cancel_while_fulfilled":
                c.execute("UPDATE orders SET status='FULFILLED' WHERE id=?", (oid,))
        for product_id, kind, delta in movements:
            c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,?,?,?)",
                      (product_id, delta, kind, oid, "synthetic broken import"))
            c.execute("UPDATE inventory SET available=available+? WHERE product_id=?", (delta, product_id))
    return pid, oid


@pytest.mark.parametrize("case,expected", CASES)
def test_balanced_history_is_not_healthy(api, case, expected):
    client, db = api
    _, oid = inject_balanced_history(client, db, case)
    before = client.get("/orders").json()
    report = client.get("/integrity").json()
    assert report["stock_mismatch_count"] == report["order_mismatch_count"] == 0
    assert not report["healthy"]
    assert report["ledger_mismatch_count"] == expected
    assert len(report["ledger_mismatches"]) == expected
    assert {row["order_id"] for row in report["ledger_mismatches"]} == {oid}
    assert not report["truncated"]
    assert client.get("/orders").json() == before  # diagnostics must not repair history
    if case == "missing_sale":
        assert report["ledger_mismatches"][0]["expected_delta"] == -1
        assert report["ledger_mismatches"][0]["actual_delta"] is None
    if case.startswith("orphan") or case.startswith("cancel_while"):
        assert report["ledger_mismatches"][0]["expected_delta"] is None


def test_ledger_details_are_bounded_ordered_and_reader_accessible(api):
    client, db = api
    for suffix in ["c", "a", "b"]:
        inject_balanced_history(client, db, "missing_sale", suffix)
    full = client.get("/integrity?limit=100").json()
    bounded = client.get("/integrity?limit=2", headers={"X-API-Key": "local-test-read-key"}).json()
    assert not bounded["healthy"] and bounded["truncated"]
    assert bounded["ledger_mismatch_count"] == 3
    assert bounded["ledger_mismatches"] == full["ledger_mismatches"][:2]
    assert [row["order_id"] for row in full["ledger_mismatches"]] == sorted(row["order_id"] for row in full["ledger_mismatches"])
    assert not full["truncated"]
    assert client.get("/integrity?limit=0").status_code == 422


def test_ledger_semantics_accept_normal_lifecycle_and_retries(api):
    client, _ = api
    first, second = product(client, "VALID-A", 20), product(client, "VALID-B", 20)
    for index, terminal in enumerate([None, "cancel", "fulfill"]):
        payload = {"request_key": f"lifecycle-order-{index}", "items": [
            {"product_id": first, "quantity": 2}, {"product_id": second, "quantity": 3}]}
        placed = client.post("/orders", json=payload)
        assert placed.status_code == 201
        oid = placed.json()["id"]
        assert client.post("/orders", json=payload).json()["id"] == oid
        if terminal:
            assert client.post(f"/orders/{oid}/{terminal}").status_code == 200
            assert client.post(f"/orders/{oid}/{terminal}").status_code == 200
    report = client.get("/integrity").json()
    assert report["healthy"] and report["ledger_mismatch_count"] == 0
    assert report["ledger_mismatches"] == [] and not report["truncated"]


def test_ledger_counts_and_details_share_one_read_snapshot(api, monkeypatch):
    client, db = api
    pid = product(client, "SNAPSHOT-SEMANTIC")
    original = db.read_snapshot
    injected = False

    class Proxy:
        def __init__(self, c):
            self.c = c

        def execute(self, statement, *args):
            nonlocal injected
            cursor = self.c.execute(statement, *args)
            if "AS ledger_mismatches" in statement and "COUNT(*)" in statement and not injected:
                injected = True
                with db.transaction() as writer:
                    for kind, delta in [("SALE", -1), ("ADJUST", 1)]:
                        writer.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,?,?,?)",
                                       (pid, delta, kind, "snapshot-orphan", "synthetic"))
            return cursor

    @contextmanager
    def traced_snapshot():
        with original() as c:
            yield Proxy(c)

    monkeypatch.setattr(db, "read_snapshot", traced_snapshot)
    first = client.get("/integrity").json()
    assert injected
    assert first["healthy"] and first["ledger_mismatch_count"] == 0 and first["ledger_mismatches"] == []
    second = client.get("/integrity").json()
    assert not second["healthy"] and second["ledger_mismatch_count"] == len(second["ledger_mismatches"]) == 1


def test_openapi_exposes_ledger_diagnostic_contract(api):
    client, _ = api
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert {"ledger_mismatch_count", "ledger_mismatches"} <= set(schemas["IntegrityRead"]["required"])
    assert {"order_id", "product_id", "kind", "expected_delta", "actual_delta"} <= set(schemas["LedgerMismatch"]["required"])
