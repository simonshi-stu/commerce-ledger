-- v3: opt-in synthetic checkout holds; legacy order rows are unchanged.
CREATE TABLE stock_reservations (
 id TEXT PRIMARY KEY,
 request_key TEXT NOT NULL UNIQUE,
 product_id INTEGER NOT NULL REFERENCES products(id),
 quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity BETWEEN 1 AND 10000),
 unit_price_cents INTEGER NOT NULL CHECK(typeof(unit_price_cents)='integer' AND unit_price_cents BETWEEN 1 AND 100000000),
 ttl_seconds INTEGER NOT NULL CHECK(ttl_seconds BETWEEN 1 AND 3600),
 expires_at INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('HELD','PAID','EXPIRED')),
 order_id TEXT UNIQUE REFERENCES orders(id),
 CHECK((state='PAID' AND order_id IS NOT NULL AND order_id=id) OR
       (state IN ('HELD','EXPIRED') AND order_id IS NULL))
);
CREATE INDEX idx_reservations_due ON stock_reservations(expires_at,id) WHERE state='HELD';
CREATE INDEX idx_reservations_product ON stock_reservations(product_id) WHERE state='HELD';
CREATE TABLE simulated_payment_receipts (
 event_id TEXT PRIMARY KEY,
 reservation_id TEXT NOT NULL REFERENCES stock_reservations(id),
 amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents>0),
 outcome TEXT NOT NULL CHECK(outcome IN ('PAID','ALREADY_PAID','LATE_PAYMENT')),
 order_id TEXT REFERENCES orders(id),
 CHECK((outcome='LATE_PAYMENT' AND order_id IS NULL) OR
       (outcome IN ('PAID','ALREADY_PAID') AND order_id IS NOT NULL AND order_id=reservation_id))
);
CREATE UNIQUE INDEX idx_reservation_paid_once ON simulated_payment_receipts(reservation_id) WHERE outcome='PAID';

-- The regular stock adjustment endpoint must leave space to return current holds.
CREATE TRIGGER inventory_hold_headroom
BEFORE UPDATE OF available ON inventory
WHEN NEW.available>OLD.available AND NEW.available>1000000000-(
 SELECT COALESCE(SUM(quantity),0) FROM stock_reservations
 WHERE product_id=NEW.product_id AND state='HELD'
)
BEGIN
 SELECT RAISE(ABORT, 'stock adjustment would displace active holds');
END;
