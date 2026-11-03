"""完全模拟数据；重复执行不改价格、不补库存、不重置业务数据。"""
import os
from pathlib import Path
from .db import Database

PRODUCTS = [
    ("DEMO-MUG-001", "模拟陶瓷杯", 2990, 20),
    ("DEMO-BAG-002", "模拟帆布袋", 4590, 12),
    ("DEMO-LAMP-003", "模拟阅读灯", 8990, 5),
    ("DEMO-BOOK-004", "模拟笔记本", 1290, 30),
    ("DEMO-RARE-005", "模拟限量礼盒", 15900, 1),
]


def seed(db):
    db.initialize(allow_upgrade=False)
    with db.transaction() as c:
        for sku, name, price, quantity in PRODUCTS:
            row = c.execute("SELECT id FROM products WHERE sku=?", (sku,)).fetchone()
            if row:
                continue
            product_id = c.execute("INSERT INTO products(sku,name,price_cents) VALUES(?,?,?)", (sku, name, price)).lastrowid
            c.execute("INSERT INTO inventory VALUES(?,?)", (product_id, quantity))
            c.execute("INSERT INTO stock_movements(product_id,delta,kind,reference,reason) VALUES(?,?,'ADJUST','synthetic-seed-v1','完全模拟初始库存')",
                      (product_id, quantity))


if __name__ == "__main__":
    path = os.environ.get("COMMERCE_DB", str(Path(__file__).resolve().parent.parent / "data" / "commerce.db"))
    seed(Database(path))
    print("Synthetic demo data ready:", path)

