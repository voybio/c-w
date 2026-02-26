from __future__ import annotations

import base64
import json
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class PaymentIntentResult:
    provider: str
    purchase_id: str
    payment_url: str
    client_secret: str | None
    status: str
    provider_txn_id: str | None = None


class PaymentGateway:
    def create_one_time_intent(
        self,
        provider: str,
        amount_usd: float,
        agent_id: str,
        tier: str,
        message: str,
        success_url: str | None,
        cancel_url: str | None,
        paypal_card: dict | None,
        inline_capture_preferred: bool,
    ) -> PaymentIntentResult:
        purchase_id = f"pur_{uuid.uuid4().hex[:18]}"

        if provider == "stripe":
            base = os.getenv("STRIPE_CHECKOUT_URL", "https://checkout.stripe.com/pay/mock")
            url = f"{base}?purchase_id={purchase_id}&tier={tier}"
            return PaymentIntentResult(provider, purchase_id, url, f"cs_mock_{purchase_id}", "pending")

        if provider == "paypal":
            if inline_capture_preferred and paypal_card:
                safe_agent = agent_id.replace("|", "/")[:32]
                safe_message = message.replace("|", "/")[:64]
                memo = f"{safe_agent}|{tier}|{safe_message}"[:127]
                captured = self._try_paypal_inline_capture(
                    purchase_id=purchase_id,
                    amount_usd=amount_usd,
                    memo=memo,
                    paypal_card=paypal_card,
                )
                if captured is not None:
                    return PaymentIntentResult(
                        provider="paypal",
                        purchase_id=purchase_id,
                        payment_url="",
                        client_secret=None,
                        status="completed",
                        provider_txn_id=captured,
                    )

            base = os.getenv("PAYPAL_CHECKOUT_URL", "https://www.paypal.com/checkoutnow")
            url = f"{base}?token={purchase_id}&tier={tier}"
            return PaymentIntentResult(provider, purchase_id, url, None, "pending")

        raise ValueError("Unsupported provider")

    def _paypal_api_base(self) -> str:
        mode = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()
        if mode == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    def _paypal_token(self) -> str | None:
        client_id = os.getenv("PAYPAL_CLIENT_ID", "")
        client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            return None

        creds = f"{client_id}:{client_secret}".encode("utf-8")
        basic = base64.b64encode(creds).decode("ascii")
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")

        req = urllib.request.Request(
            url=f"{self._paypal_api_base()}/v1/oauth2/token",
            method="POST",
            data=body,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return str(payload.get("access_token", "")) or None
        except (urllib.error.URLError, TimeoutError, ValueError):
            return None

    def _try_paypal_inline_capture(self, purchase_id: str, amount_usd: float, memo: str, paypal_card: dict) -> str | None:
        token = self._paypal_token()
        if token is None:
            return None

        amount_value = f"{amount_usd:.2f}"
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": purchase_id,
                    "custom_id": memo,
                    "amount": {"currency_code": "USD", "value": amount_value},
                }
            ],
            "payment_source": {
                "card": {
                    "number": str(paypal_card.get("number", "")),
                    "expiry": str(paypal_card.get("expiry", "")),
                    "security_code": str(paypal_card.get("security_code", "")),
                    "name": str(paypal_card.get("name", "")),
                }
            },
        }

        req = urllib.request.Request(
            url=f"{self._paypal_api_base()}/v2/checkout/orders",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": purchase_id,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return None

        status = str(result.get("status", "")).upper()
        if status == "COMPLETED":
            return self._extract_paypal_capture_id(result) or str(result.get("id", "")) or purchase_id

        order_id = str(result.get("id", ""))
        if status in {"CREATED", "APPROVED"} and order_id:
            capture_req = urllib.request.Request(
                url=f"{self._paypal_api_base()}/v2/checkout/orders/{order_id}/capture",
                method="POST",
                data=b"{}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": f"{purchase_id}-capture",
                },
            )
            try:
                with urllib.request.urlopen(capture_req, timeout=20) as resp:
                    capture_result = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, ValueError):
                return None

            if str(capture_result.get("status", "")).upper() == "COMPLETED":
                return self._extract_paypal_capture_id(capture_result) or order_id

        return None

    def _extract_paypal_capture_id(self, payload: dict) -> str | None:
        units = payload.get("purchase_units", [])
        if not isinstance(units, list):
            return None
        for unit in units:
            if not isinstance(unit, dict):
                continue
            payments = unit.get("payments", {})
            if not isinstance(payments, dict):
                continue
            captures = payments.get("captures", [])
            if not isinstance(captures, list):
                continue
            for capture in captures:
                if not isinstance(capture, dict):
                    continue
                capture_id = str(capture.get("id", "")).strip()
                if capture_id:
                    return capture_id
        return None
