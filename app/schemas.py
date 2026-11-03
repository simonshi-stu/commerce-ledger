from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sku = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9][A-Z0-9_-]{0,39}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,80}$")]
PositiveId = Annotated[int, Field(strict=True, gt=0)]
Price = Annotated[int, Field(strict=True, ge=1, le=100000000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductCreate(Input):
    sku: Sku
    name: Name
    price_cents: Price


class ProductUpdate(Input):
    name: Name
    price_cents: Price


class Adjustment(Input):
    delta: Annotated[int, Field(strict=True, ge=-1000000000, le=1000000000)]
    request_key: Key
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

    @model_validator(mode="after")
    def nonzero(self):
        if self.delta == 0:
            raise ValueError("库存调整量不能为零")
        return self


class OrderItem(Input):
    product_id: PositiveId
    quantity: Annotated[int, Field(strict=True, ge=1, le=10000)]


class OrderCreate(Input):
    request_key: Key
    items: list[OrderItem] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("同一订单中商品不能重复，请先合并数量")
        return self


class ProductRead(BaseModel):
    id: int
    sku: str
    name: str
    price_cents: int
    active: Literal[0, 1]
    available: int


class MovementRead(BaseModel):
    id: int
    product_id: int
    delta: int
    kind: Literal["ADJUST", "SALE", "CANCEL"]
    reference: str
    reason: str
    created_at: str


class OrderSummary(BaseModel):
    id: str
    status: Literal["PLACED", "CANCELLED", "FULFILLED"]
    total_cents: int
    created_at: str


class OrderItemRead(BaseModel):
    product_id: int
    quantity: int
    unit_price_cents: int


class OrderRead(OrderSummary):
    items: list[OrderItemRead]

