from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET"]


@dataclass
class OrderIntent:
    symbol: str
    side: OrderSide
    order_type: OrderType
    volume: float
    price: float
    tp: float | None = None
    comment: str = ""
    position_ticket: int | None = None
