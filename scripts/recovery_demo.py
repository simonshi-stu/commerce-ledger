"""A disposable backup/recovery exercise using synthetic data only."""
from pathlib import Path
from tempfile import TemporaryDirectory

from app import service
from app.db import Database
from app.recovery import snapshot_database, verify_database
from app.schemas import Adjustment, OrderCreate, ProductCreate


def main():
    with TemporaryDirectory(prefix="commerce-recovery-demo-") as directory:
        folder = Path(directory)
        original = Database(folder / "original.db")
        original.initialize()
        product = service.create_product(original, ProductCreate(
            sku="RECOVERY-DEMO", name="synthetic cup", price_cents=2990))
        pid = product["id"]
        service.adjust_stock(original, pid, Adjustment(
            delta=5, request_key="demo-stock-001", reason="synthetic"))
        payload = OrderCreate(request_key="demo-order-001", items=[{"product_id": pid, "quantity": 2}])
        order = service.place_order(original, payload)
        backup, restored = folder / "backup.db", folder / "restored.db"
        snapshot_database(original.path, backup)
        service.transition_order(original, order["id"], "CANCELLED")
        snapshot_database(backup, restored)
        recovered = Database(restored)
        assert service.place_order(recovered, payload) == order
        with recovered.connection() as connection:
            assert service.require_product(connection, pid)["available"] == 3
        assert verify_database(restored)["verified"]
        try:
            snapshot_database(backup, restored)
        except FileExistsError:
            pass
        else:
            raise AssertionError("existing recovery file was overwritten")
        print("Recovery demo passed: verified snapshot; later cancellation excluded; original order/price/stock restored; retry did not duplicate sale; overwrite refused")


if __name__ == "__main__":
    main()
