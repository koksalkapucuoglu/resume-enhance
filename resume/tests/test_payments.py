"""Tests for one-time Pro purchases: grants, webhooks and expiry."""

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from resume.models import Purchase
from resume.services import payment_service

SECRET = "test-webhook-secret"

PLANS = [
    {"code": "pro_3m", "name": "Pro · 3 months", "days": 90, "price_display": "$9",
     "blurb": "", "checkout_url": "https://pay.example.com/3m",
     "provider_product_id": "prod_3m", "highlight": True},
    {"code": "pro_12m", "name": "Pro · 12 months", "days": 365, "price_display": "$24",
     "blurb": "", "checkout_url": "", "provider_product_id": "prod_12m",
     "highlight": False},
]


def order_payload(user_id, order_id="ord_1", plan="pro_3m", total=900, status="paid"):
    return json.dumps({
        "meta": {"event_name": "order_created",
                 "custom_data": {"user_id": str(user_id), "plan": plan}},
        "data": {"id": order_id, "attributes": {"status": status, "total": total,
                                                "currency": "USD"}},
    }).encode()


def sign(body):
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@override_settings(
    PREMIUM_PLANS=PLANS,
    PAYMENTS={"PROVIDER": "lemonsqueezy", "WEBHOOK_SECRET": SECRET},
)
class PremiumGrantTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.profile = self.user.profile

    def test_free_by_default(self):
        self.assertFalse(self.profile.is_pro())
        self.assertEqual(self.profile.premium_days_left, 0)

    def test_grant_makes_the_account_pro(self):
        self.profile.grant_premium(90)
        self.assertTrue(self.profile.is_pro())
        self.assertGreater(self.profile.premium_days_left, 88)

    def test_expired_access_falls_back_to_free(self):
        self.profile.premium_until = timezone.now() - timedelta(days=1)
        self.profile.save()
        self.assertFalse(self.profile.is_pro())
        self.assertEqual(self.profile.premium_days_left, 0)

    def test_buying_again_extends_rather_than_replaces(self):
        first = self.profile.grant_premium(90)
        second = self.profile.grant_premium(90)
        self.assertAlmostEqual((second - first).days, 90, delta=1)

    def test_buying_after_expiry_starts_from_now(self):
        self.profile.premium_until = timezone.now() - timedelta(days=30)
        self.profile.save()
        granted = self.profile.grant_premium(90)
        self.assertAlmostEqual((granted - timezone.now()).days, 89, delta=1)

    def test_staff_granted_tier_ignores_the_clock(self):
        self.profile.tier = "pro"
        self.profile.save()
        self.assertTrue(self.profile.is_pro())
        self.assertIsNone(self.profile.premium_days_left)


@override_settings(
    PREMIUM_PLANS=PLANS,
    PAYMENTS={"PROVIDER": "lemonsqueezy", "WEBHOOK_SECRET": SECRET},
)
class RecordPurchaseTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")

    def test_records_and_grants(self):
        purchase, created = payment_service.record_purchase(
            self.user, "lemonsqueezy", "ord_1", "pro_3m", 900, "USD"
        )
        self.assertTrue(created)
        self.assertEqual(purchase.days_granted, 90)
        self.assertTrue(self.user.profile.is_pro())

    def test_a_redelivered_webhook_grants_nothing_extra(self):
        payment_service.record_purchase(self.user, "lemonsqueezy", "ord_1", "pro_3m")
        first_until = self.user.profile.premium_until

        _, created = payment_service.record_purchase(
            self.user, "lemonsqueezy", "ord_1", "pro_3m"
        )
        self.assertFalse(created)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.premium_until, first_until)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_a_different_order_does_grant_again(self):
        payment_service.record_purchase(self.user, "lemonsqueezy", "ord_1", "pro_3m")
        payment_service.record_purchase(self.user, "lemonsqueezy", "ord_2", "pro_3m")
        self.assertEqual(Purchase.objects.count(), 2)
        self.assertGreater(self.user.profile.premium_days_left, 178)

    def test_plan_can_be_matched_by_product_name(self):
        purchase, _ = payment_service.record_purchase(
            self.user, "lemonsqueezy", "ord_1", "Pro · 3 months"
        )
        self.assertEqual(purchase.plan, "pro_3m")

    def test_unknown_plan_is_refused(self):
        with self.assertRaises(payment_service.PaymentError):
            payment_service.record_purchase(self.user, "lemonsqueezy", "ord_1", "nope")
        self.assertFalse(self.user.profile.is_pro())


@override_settings(
    PREMIUM_PLANS=PLANS,
    PAYMENTS={"PROVIDER": "lemonsqueezy", "WEBHOOK_SECRET": SECRET},
)
class WebhookTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.url = reverse("resume:payment_webhook")

    def post(self, body, signature=None):
        return self.client.post(
            self.url, body, content_type="application/json",
            HTTP_X_SIGNATURE=signature if signature is not None else sign(body),
        )

    def test_valid_order_grants_access(self):
        resp = self.post(order_payload(self.user.id))
        self.assertEqual(resp.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_pro())

    def test_unsigned_request_is_rejected(self):
        resp = self.post(order_payload(self.user.id), signature="")
        self.assertEqual(resp.status_code, 401)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_pro())

    def test_forged_signature_is_rejected(self):
        resp = self.post(order_payload(self.user.id), signature="deadbeef")
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(Purchase.objects.exists())

    def test_tampered_body_fails_verification(self):
        body = order_payload(self.user.id)
        good = sign(body)
        tampered = order_payload(self.user.id, plan="pro_12m")
        self.assertEqual(self.post(tampered, signature=good).status_code, 401)

    def test_unpaid_order_grants_nothing(self):
        resp = self.post(order_payload(self.user.id, status="pending"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_unrelated_event_is_acknowledged_but_ignored(self):
        body = json.dumps({"meta": {"event_name": "subscription_updated"},
                           "data": {"id": "x", "attributes": {}}}).encode()
        self.assertEqual(self.post(body).status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_payload_without_a_user_is_a_bad_request(self):
        body = json.dumps({
            "meta": {"event_name": "order_created", "custom_data": {}},
            "data": {"id": "ord_9", "attributes": {"status": "paid"}},
        }).encode()
        self.assertEqual(self.post(body).status_code, 400)

    def test_unknown_user_is_acknowledged_not_retried(self):
        """Returning an error would make the provider redeliver forever."""
        resp = self.post(order_payload(999999))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_non_json_body_is_a_bad_request(self):
        self.assertEqual(self.post(b"not json").status_code, 400)

    def test_redelivery_over_http_is_idempotent(self):
        body = order_payload(self.user.id)
        self.post(body)
        self.post(body)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_webhook_needs_no_login_or_csrf_token(self):
        """The caller is the provider; the signature is the authentication."""
        self.assertEqual(self.post(order_payload(self.user.id)).status_code, 200)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    @override_settings(PAYMENTS={"PROVIDER": "lemonsqueezy", "WEBHOOK_SECRET": ""})
    def test_missing_secret_refuses_everything(self):
        body = order_payload(self.user.id)
        resp = self.client.post(self.url, body, content_type="application/json",
                                HTTP_X_SIGNATURE=sign(body))
        self.assertEqual(resp.status_code, 401)


@override_settings(
    PREMIUM_PLANS=PLANS,
    PAYMENTS={"PROVIDER": "lemonsqueezy", "WEBHOOK_SECRET": SECRET},
)
class PricingAndCheckoutTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x", email="ada@example.com")
        self.client.force_login(self.user)

    def test_pricing_lists_the_plans(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("Pro · 3 months", html)
        self.assertIn("$24", html)

    def test_plan_without_a_checkout_url_shows_as_unavailable(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("not set up yet", html)

    def test_checkout_redirects_to_the_provider_with_the_user_attached(self):
        resp = self.client.post(reverse("resume:start_checkout", args=["pro_3m"]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("pay.example.com", resp["Location"])
        self.assertIn(f"user_id]={self.user.id}", resp["Location"])

    def test_unconfigured_plan_returns_to_pricing(self):
        resp = self.client.post(reverse("resume:start_checkout", args=["pro_12m"]))
        self.assertRedirects(resp, reverse("resume:pricing"))

    def test_unknown_plan_returns_to_pricing(self):
        resp = self.client.post(reverse("resume:start_checkout", args=["nope"]))
        self.assertRedirects(resp, reverse("resume:pricing"))

    def test_checkout_requires_post(self):
        resp = self.client.get(reverse("resume:start_checkout", args=["pro_3m"]))
        self.assertEqual(resp.status_code, 405)

    def test_pricing_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("resume:pricing")).status_code, 302)

    def test_pro_user_sees_their_expiry(self):
        self.user.profile.grant_premium(90)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("days left", html)


@override_settings(
    PREMIUM_PLANS=PLANS,
    PAYMENTS={"PROVIDER": "lemonsqueezy", "WEBHOOK_SECRET": SECRET},
)
class PurchasedAccessUnlocksFeaturesTest(TestCase):
    """A bought period must unlock the same things a staff-set tier does."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def test_quotas_lift(self):
        profile = self.user.profile
        profile.import_count = 99
        profile.download_count = 99
        profile.agent_message_count = 99
        profile.save()
        self.assertFalse(profile.can_import())

        profile.grant_premium(90)
        self.assertTrue(profile.can_import())
        self.assertTrue(profile.can_download())
        self.assertTrue(profile.can_send_agent_message())
        self.assertTrue(profile.can_create_resume())

    def test_pro_tools_become_available(self):
        from resume.services import agent_tools

        before = {s["function"]["name"] for s in agent_tools.tool_schemas(self.user)}
        self.assertNotIn("match_job", before)

        self.user.profile.grant_premium(90)
        after = {s["function"]["name"] for s in agent_tools.tool_schemas(self.user)}
        self.assertIn("match_job", after)

    def test_job_tracker_opens(self):
        html = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertIn("Pro", html)

        self.user.profile.grant_premium(90)
        html = self.client.get(reverse("resume:jobs")).content.decode()
        self.assertNotIn("jobs_pro_title", html)
