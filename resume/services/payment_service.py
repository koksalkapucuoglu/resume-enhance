"""
One-time purchases of Pro access, sold through Paddle.

Not a subscription: the user buys a fixed period, it runs out, and their data
stays. Resume writing is episodic — people finish a job search and leave — so
billing them monthly mostly generates cancellations.

Paddle is the merchant of record: it is the seller on the receipt, collects
and remits VAT/KDV, and accepts an individual in Türkiye as the vendor, so no
company is needed to sell. Card details never reach this app — Paddle.js opens
the checkout over our page and the outcome arrives on the webhook.

Nothing outside this module knows the provider. Swapping it means a new
provider class with the same three methods and two settings.
"""

import hashlib
import hmac
import json
import logging
import time

from django.conf import settings

from resume.models import Purchase

logger = logging.getLogger(__name__)

# How old a webhook signature may be. Paddle's SDKs default to five seconds;
# redeliveries are signed afresh and the order id makes a replay a no-op, so a
# wider window only absorbs clock drift.
SIGNATURE_TOLERANCE_SECONDS = 300


class PaymentError(Exception):
    """Raised when a webhook cannot be trusted or understood."""


# PAYMENT_STATUS values:
#   coming_soon — plans shown, no checkout, webhook closed (the default)
#   test        — checkout only for staff accounts, webhook open: a sandbox
#                 purchase can be tested on the live site while every other
#                 visitor still sees "not on sale yet"
#   live        — checkout for everyone
STATUS_COMING_SOON, STATUS_TEST, STATUS_LIVE = "coming_soon", "test", "live"


def _configured():
    payments = settings.PAYMENTS
    return bool(payments.get("CLIENT_TOKEN")) and bool(payments.get("WEBHOOK_SECRET"))


def is_live():
    """
    Whether everyone can buy.

    Needs the switch *and* the keys: a half-configured environment shows the
    plans as coming soon instead of a buy button that cannot work.
    """
    return settings.PAYMENTS.get("STATUS") == STATUS_LIVE and _configured()


def is_test_mode():
    return settings.PAYMENTS.get("STATUS") == STATUS_TEST and _configured()


def checkout_open_for(user):
    """Whether this visitor gets a buy button: everyone when live, staff in test mode."""
    if is_live():
        return True
    return is_test_mode() and bool(user and user.is_authenticated and user.is_staff)


def accepts_webhooks():
    """The webhook grants access in test mode too — that is what the test is for."""
    return is_live() or is_test_mode()


def plans():
    """Purchasable plans, in display order."""
    return settings.PREMIUM_PLANS


def get_plan(code):
    return next((p for p in plans() if p["code"] == code), None)


def plan_for_price(price_id):
    """The plan a Paddle price belongs to — decided here, never by the client."""
    if not price_id:
        return None
    return next((p for p in plans() if p.get("price_id") == price_id), None)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PaddleProvider:
    """Paddle Billing: overlay checkout in the browser, signed webhooks."""

    name = "paddle"

    def checkout_config(self, user):
        """What the pricing page hands to Paddle.js. Nothing here is secret."""
        return {
            "environment": settings.PAYMENTS.get("ENVIRONMENT", "sandbox"),
            "token": settings.PAYMENTS.get("CLIENT_TOKEN", ""),
            "email": user.email or "",
            # Paddle echoes this back on the webhook so the payment finds its
            # account. Tampering with it only gifts Pro to someone else.
            "custom_data": {"user_id": str(user.id)},
        }

    def verify(self, body, headers):
        """Reject anything not signed with the endpoint's secret key."""
        secret = settings.PAYMENTS.get("WEBHOOK_SECRET")
        if not secret:
            raise PaymentError("No webhook secret configured.")

        header = headers.get("Paddle-Signature") or ""
        parts = dict(
            item.split("=", 1) for item in header.split(";") if "=" in item
        )
        ts, signature = parts.get("ts", ""), parts.get("h1", "")
        if not ts.isdigit() or not signature:
            raise PaymentError("Missing or malformed Paddle-Signature header.")
        if abs(time.time() - int(ts)) > SIGNATURE_TOLERANCE_SECONDS:
            raise PaymentError("Signature timestamp outside tolerance.")

        expected = hmac.new(
            secret.encode(), f"{ts}:".encode() + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PaymentError("Signature mismatch.")

    def parse(self, body):
        """
        Pull out what a completed one-time payment needs.

        Returns None for events that grant nothing, so the caller acknowledges
        them without acting (an error answer makes Paddle retry).
        """
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            raise PaymentError("Body is not JSON.")
        if not isinstance(payload, dict):
            raise PaymentError("Body is not a JSON object.")

        if payload.get("event_type") != "transaction.completed":
            return None
        data = payload.get("data") or {}
        if data.get("status") != "completed":
            return None

        custom = data.get("custom_data") or {}
        try:
            user_id = int(custom.get("user_id"))
        except (TypeError, ValueError):
            raise PaymentError("Payload carries no usable user_id.")

        external_id = str(data.get("id") or "")
        if not external_id:
            raise PaymentError("Payload carries no transaction id.")

        items = data.get("items") or []
        price_id = ((items[0] or {}).get("price") or {}).get("id") if items else None
        plan = plan_for_price(price_id)
        if not plan:
            raise PaymentError(f"Unknown price: {price_id!r}")

        totals = (data.get("details") or {}).get("totals") or {}
        try:
            amount = int(totals.get("total") or 0)
        except (TypeError, ValueError):
            amount = 0
        return {
            "user_id": user_id,
            "external_id": external_id,
            "plan_code": plan["code"],
            "amount_cents": amount,
            "currency": str(data.get("currency_code") or "USD"),
        }


_PROVIDERS = {PaddleProvider.name: PaddleProvider}


def get_provider(name=None):
    name = name or settings.PAYMENTS.get("PROVIDER", PaddleProvider.name)
    provider_class = _PROVIDERS.get(name)
    if not provider_class:
        raise PaymentError(f"Unknown payment provider: {name}")
    return provider_class()


# ---------------------------------------------------------------------------
# Granting access
# ---------------------------------------------------------------------------


def record_purchase(user, provider_name, external_id, plan_code, amount_cents=0,
                    currency="USD"):
    """
    Grant a purchased period, once.

    Providers retry webhooks; the unique external_id makes a redelivery a no-op
    rather than a second period. Returns (purchase, created).
    """
    plan = get_plan(plan_code)
    if not plan:
        raise PaymentError(f"Unknown plan: {plan_code!r}")

    existing = Purchase.objects.filter(external_id=external_id).first()
    if existing:
        logger.info("Ignoring duplicate purchase webhook for %s", external_id)
        return existing, False

    granted_until = user.profile.grant_premium(plan["days"])
    purchase = Purchase.objects.create(
        user=user,
        provider=provider_name,
        external_id=external_id,
        plan=plan["code"],
        days_granted=plan["days"],
        amount_cents=amount_cents,
        currency=currency,
        granted_until=granted_until,
    )
    logger.info(
        "Granted %s days to user %s (plan %s, order %s)",
        plan["days"], user.id, plan["code"], external_id,
    )
    return purchase, True
