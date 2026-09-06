"""
One-time purchases of Pro access.

Not a subscription: the user buys a fixed period, it runs out, and their data
stays. Resume writing is episodic — people finish a job search and leave — so
billing them monthly mostly generates cancellations.

The provider is behind a small adapter. LemonSqueezy is the first one because
it acts as merchant of record (it handles VAT, which a solo operator selling
into the EU and Turkey otherwise has to), but nothing above this module knows
that: swapping providers means a new adapter and two settings.
"""

import hashlib
import hmac
import json
import logging

from django.conf import settings

from resume.models import Purchase

logger = logging.getLogger(__name__)


class PaymentError(Exception):
    """Raised when a webhook cannot be trusted or understood."""


def plans():
    """Purchasable plans, in display order."""
    return settings.PREMIUM_PLANS


def get_plan(code):
    return next((p for p in plans() if p["code"] == code), None)


# ---------------------------------------------------------------------------
# Provider adapter
# ---------------------------------------------------------------------------


class LemonSqueezyProvider:
    """
    Checkout links and webhook verification for LemonSqueezy.

    Checkout is a hosted link per plan (configured in settings), with the user
    passed through as custom data so the webhook can find them again. That
    keeps card details entirely on the provider's side — this app never sees
    them.
    """

    name = "lemonsqueezy"

    def checkout_url(self, plan, user):
        base = plan.get("checkout_url")
        if not base:
            return None
        separator = "&" if "?" in base else "?"
        return (
            f"{base}{separator}checkout[custom][user_id]={user.id}"
            f"&checkout[email]={user.email}"
            if user.email
            else f"{base}{separator}checkout[custom][user_id]={user.id}"
        )

    def verify(self, body, headers):
        """Reject anything not signed with the configured secret."""
        secret = settings.PAYMENTS.get("WEBHOOK_SECRET")
        if not secret:
            raise PaymentError("No webhook secret configured.")

        signature = headers.get("X-Signature") or headers.get("HTTP_X_SIGNATURE") or ""
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PaymentError("Signature mismatch.")

    def parse(self, body):
        """
        Pull the fields we need out of the provider's payload.

        Returns None for events that are not a completed one-time payment, so
        the caller can acknowledge them without acting.
        """
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            raise PaymentError("Body is not JSON.")

        meta = payload.get("meta") or {}
        if meta.get("event_name") not in ("order_created",):
            return None

        data = payload.get("data") or {}
        attributes = data.get("attributes") or {}
        if attributes.get("status") not in ("paid", "completed"):
            return None

        custom = (meta.get("custom_data") or {})
        try:
            user_id = int(custom.get("user_id"))
        except (TypeError, ValueError):
            raise PaymentError("Payload carries no usable user_id.")

        external_id = str(data.get("id") or attributes.get("identifier") or "")
        if not external_id:
            raise PaymentError("Payload carries no order id.")

        return {
            "user_id": user_id,
            "external_id": external_id,
            "plan_code": str(custom.get("plan") or attributes.get("first_order_item", {}).get("product_name") or ""),
            "amount_cents": int(attributes.get("total") or 0),
            "currency": str(attributes.get("currency") or "USD"),
        }


_PROVIDERS = {LemonSqueezyProvider.name: LemonSqueezyProvider}


def get_provider(name=None):
    name = name or settings.PAYMENTS.get("PROVIDER", LemonSqueezyProvider.name)
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
    plan = get_plan(plan_code) or _plan_by_alias(plan_code)
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


def _plan_by_alias(value):
    """Match a provider's product name back to a plan when the code is absent."""
    needle = (value or "").strip().lower()
    if not needle:
        return None
    for plan in plans():
        if needle in (plan["code"].lower(), plan["name"].lower()):
            return plan
        if str(plan.get("provider_product_id") or "").lower() == needle:
            return plan
    return None
