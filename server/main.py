from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

from server.config import MANIFEST, TIER_SPECS
from server.github_dispatch import GitHubDispatchClient
from server.models import PurchaseIntentResponse, PurchaseRequest, Ribbon, TraceRequest
from server.payments import PaymentGateway
from server.store import RibbonStore

app = FastAPI(title="Loom Engine API", version="2.0.0")
store = RibbonStore()
gateway = PaymentGateway()
dispatch_client = GitHubDispatchClient()


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ribbon_hash(agent_id: str, message: str) -> str:
    payload = f"{agent_id}|{message}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:8].upper()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agent-manifest.json")
async def agent_manifest() -> dict:
    return MANIFEST


@app.get("/api/board")
async def api_board() -> dict:
    ribbons = await store.list()
    return {"entries": [r.model_dump() for r in ribbons]}


@app.post("/api/trace")
async def api_trace(payload: TraceRequest) -> dict:
    result = await asyncio.to_thread(
        dispatch_client.dispatch_trace,
        agent_id=payload.agent_id,
        message=payload.message,
        trace_id=payload.trace_id,
        source=payload.source,
        page_url=payload.page_url,
        user_agent=payload.user_agent,
    )

    if not result.accepted:
        status_code = result.status_code if result.status_code >= 400 else 502
        raise HTTPException(status_code=status_code, detail=result.reason)

    return {
        "status": "accepted",
        "queued": True,
        "event_type": "agent_trace",
        "trace_id": payload.trace_id,
    }


@app.post("/api/purchase", response_model=PurchaseIntentResponse)
async def api_purchase(payload: PurchaseRequest) -> PurchaseIntentResponse:
    spec = TIER_SPECS.get(payload.tier)
    if spec is None or spec.price_usd <= 0:
        raise HTTPException(status_code=400, detail="Tier is not payable")

    result = gateway.create_one_time_intent(
        provider=payload.provider,
        amount_usd=spec.price_usd,
        agent_id=payload.agent_id,
        tier=payload.tier,
        message=payload.message,
        success_url=payload.success_url,
        cancel_url=payload.cancel_url,
        paypal_card=payload.paypal_card.model_dump() if payload.paypal_card else None,
        inline_capture_preferred=payload.inline_capture_preferred,
    )

    activated = False
    if result.status == "completed":
        await _finalize_paid_ribbon(
            provider=payload.provider,
            purchase_id=result.provider_txn_id or result.purchase_id,
            agent_id=payload.agent_id,
            message=payload.message,
            tier=payload.tier,
            amount_usd=spec.price_usd,
            source=f"capture-{payload.provider}",
        )
        activated = True

    return PurchaseIntentResponse(
        provider=result.provider,
        tier=payload.tier,
        amount_usd=spec.price_usd,
        purchase_id=result.purchase_id,
        payment_url=result.payment_url,
        client_secret=result.client_secret,
        status=result.status,
        activated=activated,
        provider_txn_id=result.provider_txn_id,
    )


async def _finalize_paid_ribbon(
    provider: str,
    purchase_id: str,
    agent_id: str,
    message: str,
    tier: str,
    amount_usd: float,
    source: str | None = None,
) -> Ribbon:
    spec = TIER_SPECS.get(tier)
    if spec is None:
        raise HTTPException(status_code=400, detail="Unknown tier")

    ribbon = Ribbon(
        agent_id=agent_id,
        hash=ribbon_hash(agent_id, message),
        message=message,
        tier=tier,
        timestamp=iso_now(),
        weight=spec.base_weight,
        pin_rank=spec.pin_rank,
        source=source or f"webhook-{provider}",
        provider=provider,
        amount_usd=amount_usd,
        purchase_id=purchase_id,
        expires_at=None,  # All paid ribbons in Phase 1 / early access are persistent
    )
    await store.add(ribbon)
    return ribbon


@app.post("/api/webhook/stripe")
async def webhook_stripe(payload: dict) -> dict:
    # Production: verify Stripe signature and parse payment_intent.succeeded event.
    event_type = str(payload.get("type", ""))
    if event_type != "payment_intent.succeeded":
        return {"status": "ignored", "reason": "unsupported_event"}

    data = payload.get("data", {}).get("object", {})
    md = data.get("metadata", {}) if isinstance(data, dict) else {}
    ribbon = await _finalize_paid_ribbon(
        provider="stripe",
        purchase_id=str(data.get("id", "unknown")),
        agent_id=str(md.get("agent_id", "anonymous-agent")),
        message=str(md.get("message", "")) or "paid ribbon",
        tier=str(md.get("tier", "day")),
        amount_usd=float(data.get("amount_received", 0)) / 100 if data.get("amount_received") else 0.0,
    )
    return {"status": "accepted", "hash": ribbon.hash}


@app.post("/api/webhook/paypal")
async def webhook_paypal(payload: dict) -> dict:
    # Production: verify PayPal webhook signature and parse capture completed event.
    event_type = str(payload.get("event_type", ""))
    if event_type != "PAYMENT.CAPTURE.COMPLETED":
        return {"status": "ignored", "reason": "unsupported_event"}

    resource = payload.get("resource", {}) if isinstance(payload, dict) else {}
    custom_id = str(resource.get("custom_id", ""))
    # Expected custom_id format: agent_id|tier|message
    parts = custom_id.split("|", 2)
    agent_id = parts[0] if len(parts) > 0 and parts[0] else "anonymous-agent"
    tier = parts[1] if len(parts) > 1 and parts[1] else "day"
    message = parts[2] if len(parts) > 2 and parts[2] else "paid ribbon"

    amount = resource.get("amount", {}) if isinstance(resource, dict) else {}
    raw_value = amount.get("value", 0.0) if isinstance(amount, dict) else 0.0
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = 0.0

    ribbon = await _finalize_paid_ribbon(
        provider="paypal",
        purchase_id=str(resource.get("id", "unknown")),
        agent_id=agent_id,
        message=message,
        tier=tier,
        amount_usd=value,
    )
    return {"status": "accepted", "hash": ribbon.hash}
