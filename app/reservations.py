"""Single-SKU checkout holds with a simulated payment provider; no external calls."""
from time import time
from uuid import uuid4

from .service import BusinessError, require_product


def now_epoch():
    return int(time())


def get(c, reservation_id):
    row = c.execute("SELECT * FROM stock_reservations WHERE id=?", (reservation_id,)).fetchone()
    if row is None:
        raise BusinessError(404, "RESERVATION_NOT_FOUND", "库存预留不存在")
    return dict(row)


def reserve(db, data):
    with db.transaction() as c:
        old = c.execute("SELECT * FROM stock_reservations WHERE request_key=?", (data.request_key,)).fetchone()
        if old:
            if (old["product_id"], old["quantity"], old["ttl_seconds"]) != (
                data.product_id, data.quantity, data.ttl_seconds
            ):
                raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键对应不同库存预留")
            return dict(old)
        product = require_product(c, data.product_id, active=True)
        reservation_id = uuid4().hex
        updated = c.execute(
            "UPDATE inventory SET available=available-? WHERE product_id=? AND available>=?",
            (data.quantity, data.product_id, data.quantity),
        )
        if updated.rowcount != 1:
            raise BusinessError(409, "INSUFFICIENT_STOCK", "库存不足，未创建预留")
        c.execute(
            "INSERT INTO stock_reservations(id,request_key,product_id,quantity,unit_price_cents,ttl_seconds,"
            "expires_at,state) VALUES(?,?,?,?,?,?,?,'HELD')",
            (reservation_id, data.request_key, data.product_id, data.quantity, product["price_cents"],
             data.ttl_seconds, now_epoch() + data.ttl_seconds),
        )
        # SALE/CANCEL are existing ledger codes for consuming/returning sellable stock.
        c.execute(
            "INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,'SALE',?,?)",
            (data.product_id, -data.quantity, reservation_id, "待付款预留占用可售库存"),
        )
        return get(c, reservation_id)


def _expire(c, row):
    c.execute("UPDATE stock_reservations SET state='EXPIRED' WHERE id=?", (row["id"],))
    c.execute("UPDATE inventory SET available=available+? WHERE product_id=?", (row["quantity"], row["product_id"]))
    c.execute(
        "INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,'CANCEL',?,?)",
        (row["product_id"], row["quantity"], row["id"], "未付款超时释放预留"),
    )


def expire_due(db, limit):
    with db.transaction() as c:
        rows = c.execute(
            "SELECT * FROM stock_reservations WHERE state='HELD' AND expires_at<=? "
            "ORDER BY expires_at,id LIMIT ?", (now_epoch(), limit),
        ).fetchall()
        for row in rows:
            _expire(c, row)
        return {"released": len(rows)}


def settle(db, reservation_id, data):
    with db.transaction() as c:
        old = c.execute("SELECT * FROM simulated_payment_receipts WHERE event_id=?", (data.event_id,)).fetchone()
        if old:
            if old["reservation_id"] != reservation_id or old["amount_cents"] != data.amount_cents:
                raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "同一模拟支付事件对应不同预留或金额")
            return dict(old)
        row = get(c, reservation_id)
        total = row["quantity"] * row["unit_price_cents"]
        if data.amount_cents != total:
            raise BusinessError(409, "AMOUNT_MISMATCH", "付款金额与预留价格不一致")
        order_id = row["order_id"]
        if row["state"] == "PAID":
            outcome = "ALREADY_PAID"
        elif row["state"] == "EXPIRED" or now_epoch() >= row["expires_at"]:
            if row["state"] == "HELD":
                _expire(c, row)
            outcome = "LATE_PAYMENT"
        else:
            order_id = reservation_id
            # Legacy order keys only allow ASCII letters, numbers, underscore and hyphen.
            # The colon creates a private namespace for internally promoted orders.
            c.execute(
                "INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',?)",
                (order_id, f"reservation:{reservation_id}", reservation_id, total),
            )
            c.execute("INSERT INTO order_items VALUES(?,?,?,?)",
                      (order_id, row["product_id"], row["quantity"], row["unit_price_cents"]))
            c.execute("UPDATE stock_reservations SET state='PAID',order_id=? WHERE id=?",
                      (order_id, reservation_id))
            outcome = "PAID"
        c.execute(
            "INSERT INTO simulated_payment_receipts VALUES(?,?,?,?,?)",
            (data.event_id, reservation_id, data.amount_cents, outcome, order_id),
        )
        return {"event_id": data.event_id, "reservation_id": reservation_id, "amount_cents": data.amount_cents,
                "outcome": outcome, "order_id": order_id}
