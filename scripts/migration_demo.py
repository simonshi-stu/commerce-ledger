"""临时旧库升级演示；不读取或修改 data/commerce.db。"""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from app.db import Database
from app.migrate import inspect_version, migrate_database
from app.schemas import Adjustment, OrderCreate, ProductCreate
from app import service


def main():
    with TemporaryDirectory(prefix="commerce-migration-") as directory:
        db = Database(Path(directory) / "legacy.db")
        with db.connection() as conn:
            conn.executescript(Path("app/schema.sql").read_text(encoding="utf-8"))
            conn.execute("PRAGMA user_version=1")
        product = service.create_product(db, ProductCreate(sku="LEGACY-DEMO", name="synthetic legacy cup", price_cents=2990))
        service.adjust_stock(db, product["id"], Adjustment(delta=5, request_key="legacy-demo-stock", reason="synthetic"))
        order = service.place_order(db, OrderCreate(request_key="legacy-demo-order", items=[
            {"product_id": product["id"], "quantity": 2}]))
        result = migrate_database(db.path)
        assert result["from_version"] == 1 and result["to_version"] == 2
        assert inspect_version(result["backup"]) == 1
        with db.connection() as conn:
            assert service.order_detail(conn, order["id"]) == order
            assert service.require_product(conn, product["id"])["available"] == 3
        service.transition_order(db, order["id"], "FULFILLED")
        with db.connection() as conn:
            try:
                conn.execute("UPDATE orders SET status='PLACED' WHERE id=?", (order["id"],))
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("terminal order guard missing")
        assert not migrate_database(db.path)["changed"]
        print("Migration demo passed: v1 -> v2; backup readable; order/stock preserved; terminal guard enforced; repeat safe")


if __name__ == "__main__":
    main()
