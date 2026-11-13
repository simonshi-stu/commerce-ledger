import csv
import io
import pytest
from app.db import Database
from app import service
from app.order_report import order_report, as_csv, main
from app.schemas import ProductCreate, Adjustment, OrderCreate


def test_business_cohort_not_join_duplicates_or_retries(tmp_path):
    db = Database(tmp_path / 'orders.db')
    db.initialize()
    products = []
    for i, price in enumerate([1000, 2000]):
        p = service.create_product(db, ProductCreate(sku=f'REPORT-{i}', name='Synthetic', price_cents=price))
        service.adjust_stock(db, p['id'], Adjustment(delta=20, request_key=f'report-stock-{i}', reason='synthetic'))
        products.append(p['id'])
    for i, status in enumerate(['PLACED', 'FULFILLED', 'CANCELLED']):
        payload = OrderCreate(request_key=f'report-order-{i}', items=[{'product_id': p, 'quantity': 1} for p in products])
        order = service.place_order(db, payload)
        assert service.place_order(db, payload)['id'] == order['id']
        with db.transaction() as c:
            c.execute('UPDATE orders SET created_at=? WHERE id=?', ('2026-11-13T00:00:00.000Z', order['id']))
        if status != 'PLACED':
            service.transition_order(db, order['id'], status)
    report = order_report(db.path, '2026-11-13', '2026-11-14')
    assert report['total_order_count'] == 3
    assert report['non_cancelled_order_value_cents'] == 6000
    assert [r['order_value_cents'] for r in report['statuses']] == [3000]*3
    assert not report['is_payment_revenue']
    assert service.check_integrity(db, 20)['healthy']
    rows = list(csv.DictReader(io.StringIO(as_csv(report))))
    assert len(rows) == 3 and rows[-1]['status'] == 'CANCELLED'
    assert rows[0]['order_count'] == '1'
    assert order_report(db.path, '2026-11-12', '2026-11-13')['total_order_count'] == 0


def test_empty_and_readonly(tmp_path):
    db = Database(tmp_path / 'empty.db'); db.initialize()
    before = (tmp_path / 'empty.db').read_bytes()
    assert order_report(db.path, '2026-11-13', '2026-11-14')['total_order_count'] == 0
    assert (tmp_path / 'empty.db').read_bytes() == before


@pytest.mark.parametrize('start,end', [('2026-11-14','2026-11-13'), ('2026-11-13','2026-11-13'), ('20261113','2026-11-14')])
def test_invalid_range(start, end, tmp_path):
    with pytest.raises(ValueError): order_report(tmp_path/'missing.db', start, end)
    assert not (tmp_path/'missing.db').exists()


def test_missing_file_failure_is_not_created(tmp_path, capsys):
    path = tmp_path/'missing.db'
    assert main(['--database', str(path), '--start','2026-11-13','--end','2026-11-14']) == 1
    captured = capsys.readouterr()
    assert not captured.out and str(path) not in captured.err
    assert not path.exists()
