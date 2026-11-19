import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from uuid import uuid4
from typing import Annotated

from fastapi import Depends, FastAPI, Path as PathParameter, Query, Request, Security
from fastapi.security import APIKeyHeader
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .db import Database
from .schemas import (MAX_DB_INTEGER, Adjustment, OrderCreate, ProductCreate, ProductUpdate,
                      ProductRead, MovementRead, OrderSummary, OrderRead, IntegrityRead)
from . import service
from .security import KeyPolicy

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)
ProductPathId = Annotated[int, PathParameter(
    gt=0, le=MAX_DB_INTEGER, json_schema_extra={"x-maximum-integer": str(MAX_DB_INTEGER)})]


def create_app(db_path=None, api_key=None, read_api_key=None):
    db = Database(db_path or os.environ.get("COMMERCE_DB", str(ROOT / "data" / "commerce.db")))
    key = api_key if api_key is not None else os.environ.get("COMMERCE_API_KEY")
    read_key = read_api_key if read_api_key is not None else os.environ.get("COMMERCE_READ_API_KEY", "")
    policy = KeyPolicy(key, read_key)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize(allow_upgrade=False)
        yield

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def authorize(request: Request, x_api_key: str | None = Security(api_key_header)):
        return policy.authorize(x_api_key, request.method)

    app = FastAPI(title="Commerce Ledger · 本地教学后端", version="0.5.0",
                  lifespan=lifespan, dependencies=[Depends(authorize)])
    app.state.db = db

    def report_failure(request, exc):
        route = getattr(request.scope.get("route"), "path", "<unmatched>")
        log.error("request_failure request_id=%s method=%s route=%s error_type=%s",
                  request.state.request_id, request.method, route, type(exc).__name__)

    @app.middleware("http")
    async def safe_request_boundary(request, call_next):
        request.state.request_id = uuid4().hex
        try:
            response = await call_next(request)
        except Exception as exc:
            report_failure(request, exc)
            response = JSONResponse(status_code=500, content={"error": {
                "code": "INTERNAL_ERROR", "message": "服务内部错误，请提供请求编号排查",
                "request_id": request.state.request_id}})
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(service.BusinessError)
    async def business_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"location": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "details": errors}})

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse(status_code=409, content={"error": {"code": "CONSTRAINT_CONFLICT", "message": "数据违反唯一性或完整性约束"}})

    @app.exception_handler(sqlite3.OperationalError)
    async def database_error(request, exc):
        if getattr(exc, "sqlite_errorcode", None) in (5, 6) or "locked" in str(exc).lower():
            return JSONResponse(status_code=503, headers={"Retry-After": "1"},
                                content={"error": {"code": "DATABASE_BUSY", "message": "数据库繁忙，请使用原幂等键重试"}})
        report_failure(request, exc)
        return JSONResponse(status_code=500, content={"error": {
            "code": "DATABASE_ERROR", "message": "数据库操作失败",
            "request_id": request.state.request_id}})

    @app.get("/health")
    def health():
        with db.connection() as c:
            c.execute("SELECT 1")
        return {"status": "ok", "data_source": "synthetic"}

    @app.post("/products", status_code=201, response_model=ProductRead)
    def product_create(data: ProductCreate):
        return service.create_product(db, data)

    @app.get("/integrity", response_model=IntegrityRead)
    def integrity(limit: int = Query(20, ge=1, le=100)):
        return service.check_integrity(db, limit)

    @app.get("/products", response_model=list[ProductRead])
    def products(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=MAX_DB_INTEGER,
                        json_schema_extra={"x-maximum-integer": str(MAX_DB_INTEGER)})):
        return service.list_products(db, limit, offset)

    @app.get("/products/{product_id}", response_model=ProductRead)
    def product_get(product_id: ProductPathId):
        with db.connection() as c:
            return service.require_product(c, product_id)

    @app.put("/products/{product_id}", response_model=ProductRead)
    def product_update(product_id: ProductPathId, data: ProductUpdate):
        return service.update_product(db, product_id, data)

    @app.delete("/products/{product_id}", response_model=ProductRead)
    def product_delete(product_id: ProductPathId):
        return service.update_product(db, product_id)

    @app.post("/products/{product_id}/stock-adjustments", status_code=201, response_model=MovementRead)
    def stock_adjust(product_id: ProductPathId, data: Adjustment):
        return service.adjust_stock(db, product_id, data)

    @app.get("/products/{product_id}/movements", response_model=list[MovementRead])
    def movements(product_id: ProductPathId, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=MAX_DB_INTEGER,
                        json_schema_extra={"x-maximum-integer": str(MAX_DB_INTEGER)})):
        with db.connection() as c:
            service.require_product(c, product_id)
            return [dict(r) for r in c.execute("SELECT * FROM stock_movements WHERE product_id=? ORDER BY id LIMIT ? OFFSET ?",
                                               (product_id, limit, offset))]

    @app.post("/orders", status_code=201, response_model=OrderRead)
    def order_create(data: OrderCreate):
        return service.place_order(db, data)

    @app.get("/orders", response_model=list[OrderSummary])
    def orders(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=MAX_DB_INTEGER,
                        json_schema_extra={"x-maximum-integer": str(MAX_DB_INTEGER)})):
        with db.connection() as c:
            return [dict(r) for r in c.execute("SELECT id,status,total_cents,created_at FROM orders ORDER BY created_at,id LIMIT ? OFFSET ?",
                                               (limit, offset))]

    @app.get("/orders/{order_id}", response_model=OrderRead)
    def order_get(order_id: str):
        with db.connection() as c:
            return service.order_detail(c, order_id)

    @app.post("/orders/{order_id}/cancel", response_model=OrderRead)
    def order_cancel(order_id: str):
        return service.transition_order(db, order_id, "CANCELLED")

    @app.post("/orders/{order_id}/fulfill", response_model=OrderRead)
    def order_fulfill(order_id: str):
        return service.transition_order(db, order_id, "FULFILLED")

    return app

