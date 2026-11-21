"""Loopback-only synthetic merchant walkthrough; every run owns a fresh SQLite file."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep, time
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
import uvicorn

from app import reservations, service
from app.db import Database
from app.schemas import Adjustment, ProductCreate, ReservationCreate, SimulatedPayment

CATALOG = {
    "DEMO-BLK": ("黑色无框", 20, 2900),
    "DEMO-PNK": ("粉色大框", 12, 3900),
    "DEMO-CLR": ("透明轻框", 8, 4900),
}
HTML_PATH = Path(__file__).with_name("merchant_demo.html")


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: Literal["DEMO-BLK", "DEMO-PNK", "DEMO-CLR"]
    path: Literal["paid", "late"]


def snapshot(db, label, event):
    with db.read_snapshot() as conn:
        rows = conn.execute("""
            SELECT p.sku,p.name,i.available,
                COALESCE((SELECT SUM(h.quantity) FROM stock_reservations h
                     WHERE h.product_id=p.id AND h.state='HELD'),0) AS held,
                COALESCE((SELECT SUM(j.quantity) FROM order_items j JOIN orders o ON o.id=j.order_id
                     WHERE j.product_id=p.id AND o.status='PLACED'),0) AS placed
            FROM products p JOIN inventory i ON i.product_id=p.id ORDER BY p.id
        """).fetchall()
    audit = service.check_integrity(db, 20)  # private database; no intervening writers
    return {"label": label, "event": event, "stock": [dict(row) for row in rows],
            "ledger_healthy": audit["healthy"], "ledger_mismatch_count": audit["ledger_mismatch_count"]}


def wait_for_expiry(expires_at):
    # Use the real reservation boundary; no global clock overrides in the demo.
    sleep(max(0, expires_at - time()) + 0.05)


def run_scenario(config):
    with TemporaryDirectory(prefix="commerce-merchant-demo-") as folder:
        db = Database(Path(folder) / "synthetic.db")
        db.initialize()
        products = {}
        for sku, (name, qty, cents) in CATALOG.items():
            product = service.create_product(db, ProductCreate(sku=sku, name=name, price_cents=cents))
            products[sku] = product
            service.adjust_stock(db, product["id"], Adjustment(request_key=f"stock-{sku}", delta=qty,
                                                                reason="教学演示合成库存"))
        steps = [snapshot(db, "1. 初始库存", "3 个合成商品，未预留、未成单")]
        product = products[config.sku]
        hold = reservations.reserve(db, ReservationCreate(request_key="demo-reserve", product_id=product["id"],
                                                          quantity=1, ttl_seconds=60 if config.path == "paid" else 1))
        steps.append(snapshot(db, "2. 预留 1 件", "可售减 1，待付款预留加 1"))
        if config.path == "late":
            wait_for_expiry(hold["expires_at"])
            released = reservations.expire_due(db, limit=10)["released"]
            steps.append(snapshot(db, "3. 超时释放", f"扫描释放 {released} 笔，可售恢复"))
        event = SimulatedPayment(event_id="demo-payment", amount_cents=hold["unit_price_cents"])
        first = reservations.settle(db, hold["id"], event)
        replay = reservations.settle(db, hold["id"], event)
        text = ("已成单，库存不重复扣减" if first["outcome"] == "PAID"
                else "记录迟到付款；未恢复预留、未成单")
        steps.append(snapshot(db, f"{len(steps) + 1}. 模拟付款回调", text))
        steps.append(snapshot(db, f"{len(steps) + 1}. 重放同一事件", "回执一致，数量不再变化"))
        return {"synthetic": True, "sku": config.sku, "outcome": first["outcome"],
                "replay_matches": replay == first, "steps": steps}


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # The demo has no business credentials. Block non-loopback hosts and
        # require a same-origin browser request (or CLI without Origin).
        host = request.url.hostname
        origin = request.headers.get("origin")
        if (host not in {"127.0.0.1", "localhost"} or
                (origin is not None and origin != str(request.base_url).rstrip("/"))):
            return JSONResponse({"error": "Local demonstration only"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def demo_page():
        return HTML_PATH.read_text(encoding="utf-8")

    @app.post("/scenario")
    def scenario(config: Scenario):
        return run_scenario(config)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
