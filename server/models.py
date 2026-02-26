from __future__ import annotations

from pydantic import BaseModel, Field


class VisitRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=280)
    ribbon_data: dict | None = None


class PayPalCard(BaseModel):
    number: str = Field(min_length=12, max_length=24)
    expiry: str = Field(description="MM/YYYY")
    security_code: str = Field(min_length=3, max_length=4)
    name: str | None = None


class PurchaseRequest(BaseModel):
    provider: str = Field(pattern="^(stripe|paypal)$")
    tier: str = Field(pattern="^(day|3day|permanent|featured)$")
    agent_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=280)
    ribbon_data: dict | None = None
    success_url: str | None = None
    cancel_url: str | None = None
    paypal_card: PayPalCard | None = None
    inline_capture_preferred: bool = True


class Ribbon(BaseModel):
    agent_id: str
    hash: str
    message: str
    tier: str
    timestamp: str
    weight: int = 1
    pin_rank: int = 0
    source: str = "api"
    provider: str | None = None
    amount_usd: float | None = None
    purchase_id: str | None = None
    expires_at: str | None = None


class PurchaseIntentResponse(BaseModel):
    provider: str
    tier: str
    amount_usd: float
    purchase_id: str
    payment_url: str
    client_secret: str | None = None
    status: str = "pending"
    activated: bool = False
    provider_txn_id: str | None = None
