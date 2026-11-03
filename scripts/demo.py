"""不需要先启动 HTTP 服务；独立临时库完成整个教学闭环。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from app.main import create_app
from app.seed import seed

if __name__ == "__main__":
    with TemporaryDirectory(prefix="commerce-demo-") as folder:
        app = create_app(Path(folder) / "demo.db", "demo-local-key")
        with TestClient(app, headers={"X-API-Key": "demo-local-key"}) as client:
            seed(app.state.db)
            products = client.get("/products").json()
            rare = next(p for p in products if p["sku"] == "DEMO-RARE-005")
            payload = {"request_key": "demo-order-0001", "items": [{"product_id": rare["id"], "quantity": 1}]}
            response = client.post("/orders", json=payload)
            assert response.status_code == 201, response.text
            order = response.json()
            print("1. Simulated order placed:", order["id"], "CNY cents:", order["total_cents"])
            payload["request_key"] = "demo-order-0002"
            assert client.post("/orders", json=payload).status_code == 409
            print("2. Second purchase rejected: insufficient stock")
            assert client.post(f"/orders/{order['id']}/cancel").status_code == 200
            assert client.get(f"/products/{rare['id']}").json()["available"] == 1
            print("3. Cancellation restored exactly one unit; demo complete")
            audit = client.get("/integrity").json()
            assert audit["healthy"] and audit["orders_checked"] == 1
            print("4. Read-only reconciliation passed: stock ledger and order totals match")

