"""业务层：HTTP 和数据库之间的事务边界。金额统一为人民币分。"""
import hashlib
import json
from uuid import uuid4


class BusinessError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def require_product(c, product_id, active=False):
    row = c.execute("SELECT p.*,i.available FROM products p JOIN inventory i ON i.product_id=p.id WHERE p.id=?",
                    (product_id,)).fetchone()
    if row is None:
        raise BusinessError(404, "PRODUCT_NOT_FOUND", "商品不存在")
    if active and not row["active"]:
        raise BusinessError(409, "PRODUCT_INACTIVE", "商品已下架")
    return dict(row)


def create_product(db, data):
    with db.transaction() as c:
        cursor = c.execute("INSERT INTO products(sku,name,price_cents) VALUES(?,?,?)",
                           (data.sku, data.name, data.price_cents))
        c.execute("INSERT INTO inventory(product_id) VALUES(?)", (cursor.lastrowid,))
        return require_product(c, cursor.lastrowid)


def list_products(db, limit, offset):
    with db.connection() as c:
        return [dict(row) for row in c.execute(
            "SELECT p.*,i.available FROM products p JOIN inventory i ON i.product_id=p.id ORDER BY p.id LIMIT ? OFFSET ?",
            (limit, offset))]


def update_product(db, product_id, data=None):
    with db.transaction() as c:
        require_product(c, product_id)
        if data is None:  # 删除即下架，保留订单引用与审计链。
            c.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))
        else:
            c.execute("UPDATE products SET name=?,price_cents=? WHERE id=?",
                      (data.name, data.price_cents, product_id))
        return require_product(c, product_id)


def adjust_stock(db, product_id, data):
    with db.transaction() as c:
        require_product(c, product_id)
        old = c.execute("SELECT * FROM stock_movements WHERE product_id=? AND kind='ADJUST' AND reference=?",
                        (product_id, data.request_key)).fetchone()
        if old:
            if old["delta"] != data.delta or old["reason"] != data.reason:
                raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键对应不同库存调整")
            return dict(old)
        updated = c.execute("UPDATE inventory SET available=available+? WHERE product_id=? AND available+? BETWEEN 0 AND 1000000000",
                            (data.delta, product_id, data.delta))
        if updated.rowcount != 1:
            raise BusinessError(409, "INVALID_STOCK", "调整后库存不足或超过上限")
        cursor = c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,'ADJUST',?,?)",
                           (product_id, data.delta, data.request_key, data.reason))
        return dict(c.execute("SELECT * FROM stock_movements WHERE id=?", (cursor.lastrowid,)).fetchone())


def order_detail(c, order_id):
    row = c.execute("SELECT id,status,total_cents,created_at FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise BusinessError(404, "ORDER_NOT_FOUND", "订单不存在")
    result = dict(row)
    result["items"] = [dict(item) for item in c.execute(
        "SELECT product_id,quantity,unit_price_cents FROM order_items WHERE order_id=? ORDER BY product_id", (order_id,))]
    return result


def place_order(db, data):
    items = sorted((item.model_dump() for item in data.items), key=lambda item: item["product_id"])
    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    with db.transaction() as c:
        old = c.execute("SELECT id,request_hash FROM orders WHERE request_key=?", (data.request_key,)).fetchone()
        if old:
            if old["request_hash"] != digest:
                raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键对应不同订单")
            return order_detail(c, old["id"])
        order_id = uuid4().hex
        c.execute("INSERT INTO orders(id,request_key,request_hash,status,total_cents) VALUES(?,?,?,'PLACED',0)",
                  (order_id, data.request_key, digest))
        total = 0
        for item in items:
            product_id, quantity = item["product_id"], item["quantity"]
            product = require_product(c, product_id, active=True)
            # 条件扣减：不能把先查询的库存当作并发安全保证。
            updated = c.execute("UPDATE inventory SET available=available-? WHERE product_id=? AND available>=?",
                                (quantity, product_id, quantity))
            if updated.rowcount != 1:
                raise BusinessError(409, "INSUFFICIENT_STOCK", "库存不足，整单未创建")
            price = product["price_cents"]
            total += price * quantity
            c.execute("INSERT INTO order_items VALUES(?,?,?,?)", (order_id, product_id, quantity, price))
            c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,'SALE',?,'订单占用可售库存')",
                      (product_id, -quantity, order_id))
        c.execute("UPDATE orders SET total_cents=? WHERE id=?", (total, order_id))
        return order_detail(c, order_id)


def transition_order(db, order_id, target):
    if target not in ("CANCELLED", "FULFILLED"):
        raise ValueError("unsupported transition")
    with db.transaction() as c:
        order = order_detail(c, order_id)
        if order["status"] == target:
            return order
        if order["status"] != "PLACED":
            raise BusinessError(409, "INVALID_ORDER_STATE", "当前订单状态不能执行此操作")
        if target == "CANCELLED":
            for item in order["items"]:
                product_id, quantity = item["product_id"], item["quantity"]
                updated = c.execute("UPDATE inventory SET available=available+? WHERE product_id=? AND available+?<=1000000000",
                                    (quantity, product_id, quantity))
                if updated.rowcount != 1:
                    raise BusinessError(409, "INVALID_STOCK", "返还后库存超过上限")
                c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,'CANCEL',?,'取消订单返还库存')",
                          (product_id, quantity, order_id))
        c.execute("UPDATE orders SET status=? WHERE id=? AND status='PLACED'", (target, order_id))
        return order_detail(c, order_id)

