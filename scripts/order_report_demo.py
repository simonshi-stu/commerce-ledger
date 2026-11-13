"""Synthetic three-order example, exports report only; no live database is used."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from app.db import Database
from app import service
from app.schemas import ProductCreate, Adjustment, OrderCreate
from app.order_report import order_report

with TemporaryDirectory(prefix='order-report-demo-') as folder:
    db = Database(Path(folder)/'demo.db'); db.initialize()
    products = []
    for i, price in enumerate([1000, 2000]):
        p = service.create_product(db, ProductCreate(sku=f'DEMO-{i}', name='Synthetic item', price_cents=price))
        service.adjust_stock(db, p['id'], Adjustment(delta=20, request_key=f'demo-stock-{i}', reason='synthetic'))
        products.append(p['id'])
    for i, status in enumerate(['PLACED','FULFILLED','CANCELLED']):
        payload = OrderCreate(request_key=f'demo-report-{i}', items=[{'product_id': p, 'quantity': 1} for p in products])
        order = service.place_order(db, payload)
        assert service.place_order(db, payload)['id'] == order['id']
        with db.transaction() as c:
            c.execute('UPDATE orders SET created_at=? WHERE id=?', ('2026-11-13T00:00:00.000Z', order['id']))
        if status != 'PLACED': service.transition_order(db, order['id'], status)
    report = order_report(db.path, '2026-11-13','2026-11-14')
    assert report['total_order_count'] == 3
    assert report['non_cancelled_order_value_cents'] == 6000
    assert service.check_integrity(db, 20)['healthy']
    print(json.dumps(report, indent=2))
