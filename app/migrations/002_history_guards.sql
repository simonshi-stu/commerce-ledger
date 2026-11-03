-- v2: 数据库层保护终态和历史记录；无数据重写。
CREATE TRIGGER IF NOT EXISTS order_status_transition_guard
BEFORE UPDATE OF status ON orders
WHEN NEW.status <> OLD.status
 AND (OLD.status <> 'PLACED' OR NEW.status NOT IN ('CANCELLED','FULFILLED'))
BEGIN
 SELECT RAISE(ABORT, 'invalid order state transition');
END;

CREATE TRIGGER IF NOT EXISTS order_items_update_guard
BEFORE UPDATE ON order_items
BEGIN
 SELECT RAISE(ABORT, 'order items are immutable');
END;

CREATE TRIGGER IF NOT EXISTS order_items_delete_guard
BEFORE DELETE ON order_items
BEGIN
 SELECT RAISE(ABORT, 'order items are immutable');
END;

CREATE TRIGGER IF NOT EXISTS terminal_order_items_insert_guard
BEFORE INSERT ON order_items
WHEN (SELECT status FROM orders WHERE id=NEW.order_id) IN ('CANCELLED','FULFILLED')
BEGIN
 SELECT RAISE(ABORT, 'terminal order cannot accept items');
END;

CREATE TRIGGER IF NOT EXISTS stock_movements_update_guard
BEFORE UPDATE ON stock_movements
BEGIN
 SELECT RAISE(ABORT, 'stock movements are append-only');
END;

CREATE TRIGGER IF NOT EXISTS stock_movements_delete_guard
BEFORE DELETE ON stock_movements
BEGIN
 SELECT RAISE(ABORT, 'stock movements are append-only');
END;
