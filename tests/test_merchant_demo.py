import pytest
from fastapi.testclient import TestClient

from app import reservations
from scripts import merchant_demo


@pytest.mark.parametrize("path,outcome,expected", [
    ("paid", "PAID", [(20, 0, 0), (19, 1, 0), (19, 0, 1), (19, 0, 1)]),
    ("late", "LATE_PAYMENT", [(20, 0, 0), (19, 1, 0), (20, 0, 0), (20, 0, 0), (20, 0, 0)]),
])
def test_browser_demo_walkthrough_isolated(monkeypatch, path, outcome, expected):
    clock = [100]
    monkeypatch.setattr(reservations, "now_epoch", lambda: clock[0])
    monkeypatch.setattr(merchant_demo, "wait_for_expiry", lambda expires_at: clock.__setitem__(0, expires_at))
    client = TestClient(merchant_demo.create_app(), base_url="http://127.0.0.1")
    page = client.get("/")
    assert page.status_code == 200 and "已预留" in page.text
    run = client.post("/scenario", json={"sku": "DEMO-BLK", "path": path},
                      headers={"Origin": "http://127.0.0.1"})
    assert run.status_code == 200, run.text
    data = run.json()
    assert data["synthetic"] and data["outcome"] == outcome and data["replay_matches"]
    assert all(step["ledger_healthy"] and step["ledger_mismatch_count"] == 0 for step in data["steps"])
    assert [(s["stock"][0]["available"], s["stock"][0]["held"], s["stock"][0]["placed"])
            for s in data["steps"]] == expected
    assert all([r["available"] for r in s["stock"][1:]] == [12, 8] for s in data["steps"])
    again = client.post("/scenario", json={"sku": "DEMO-BLK", "path": path})
    assert again.json() == data


def test_demo_rejects_non_local_hosts_cross_origin_and_unknown_skus():
    client = TestClient(merchant_demo.create_app(), base_url="http://127.0.0.1")
    assert client.get("/", headers={"Host": "other.test"}).status_code == 403
    body = {"sku": "DEMO-PNK", "path": "paid"}
    assert client.post("/scenario", json=body, headers={"Origin": "https://other.test"}).status_code == 403
    assert client.post("/scenario", json={**body, "sku": "not-an-eyewear-sku"}).status_code == 422
