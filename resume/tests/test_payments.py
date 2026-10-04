"""Tests for one-time Pro purchases through Paddle: grants, webhooks and expiry."""

import hashlib
import hmac
import json
import time
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from resume.models import Feedback, Purchase
from resume.services import payment_service

SECRET = "pdl_ntfset_test_secret"

PLANS = [
    {"code": "pro_3m", "name": "Pro · 3 months", "days": 90, "price_display": "$9",
     "blurb": "", "price_id": "pri_3m", "highlight": True},
    {"code": "pro_12m", "name": "Pro · 12 months", "days": 365, "price_display": "$24",
     "blurb": "", "price_id": "", "highlight": False},
]

LIVE = {"STATUS": "live", "PROVIDER": "paddle", "ENVIRONMENT": "sandbox",
        "CLIENT_TOKEN": "test_client_token", "WEBHOOK_SECRET": SECRET}
COMING_SOON = {**LIVE, "STATUS": "coming_soon"}


def transaction(user_id, txn_id="txn_1", price_id="pri_3m", total="900",
                status="completed", event_type="transaction.completed"):
    return json.dumps({
        "event_id": "evt_1",
        "event_type": event_type,
        "occurred_at": "2026-10-03T10:00:00Z",
        "notification_id": "ntf_1",
        "data": {
            "id": txn_id,
            "status": status,
            "custom_data": {"user_id": str(user_id)} if user_id is not None else None,
            "currency_code": "USD",
            "items": [{"price": {"id": price_id}, "quantity": 1}],
            "details": {"totals": {"total": total}},
        },
    }).encode()


def sign(body, ts=None, secret=SECRET):
    ts = str(int(time.time()) if ts is None else ts)
    h1 = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


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


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=LIVE)
class RecordPurchaseTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")

    def test_records_and_grants(self):
        purchase, created = payment_service.record_purchase(
            self.user, "paddle", "txn_1", "pro_3m", 900, "USD"
        )
        self.assertTrue(created)
        self.assertEqual(purchase.days_granted, 90)
        self.assertTrue(self.user.profile.is_pro())

    def test_a_redelivered_webhook_grants_nothing_extra(self):
        payment_service.record_purchase(self.user, "paddle", "txn_1", "pro_3m")
        first_until = self.user.profile.premium_until
        _, created = payment_service.record_purchase(self.user, "paddle", "txn_1", "pro_3m")
        self.assertFalse(created)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.premium_until, first_until)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_a_different_order_does_grant_again(self):
        payment_service.record_purchase(self.user, "paddle", "txn_1", "pro_3m")
        payment_service.record_purchase(self.user, "paddle", "txn_2", "pro_3m")
        self.assertEqual(Purchase.objects.count(), 2)
        self.assertGreater(self.user.profile.premium_days_left, 178)

    def test_unknown_plan_is_refused(self):
        with self.assertRaises(payment_service.PaymentError):
            payment_service.record_purchase(self.user, "paddle", "txn_1", "nope")
        self.assertFalse(self.user.profile.is_pro())

    def test_the_plan_comes_from_the_price_not_the_client(self):
        self.assertEqual(payment_service.plan_for_price("pri_3m")["code"], "pro_3m")
        self.assertIsNone(payment_service.plan_for_price(""))
        self.assertIsNone(payment_service.plan_for_price("pri_unknown"))


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=LIVE)
class WebhookTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.url = reverse("resume:payment_webhook")

    def post(self, body, signature=None):
        return self.client.post(
            self.url, body, content_type="application/json",
            HTTP_PADDLE_SIGNATURE=signature if signature is not None else sign(body),
        )

    def test_completed_transaction_grants_access(self):
        resp = self.post(transaction(self.user.id))
        self.assertEqual(resp.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_pro())
        purchase = Purchase.objects.get()
        self.assertEqual((purchase.provider, purchase.plan, purchase.amount_cents),
                         ("paddle", "pro_3m", 900))

    def test_unsigned_request_is_rejected(self):
        resp = self.post(transaction(self.user.id), signature="")
        self.assertEqual(resp.status_code, 401)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_pro())

    def test_forged_signature_is_rejected(self):
        resp = self.post(transaction(self.user.id), signature=f"ts={int(time.time())};h1=deadbeef")
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(Purchase.objects.exists())

    def test_signature_with_another_secret_is_rejected(self):
        body = transaction(self.user.id)
        self.assertEqual(self.post(body, signature=sign(body, secret="other")).status_code, 401)

    def test_tampered_body_fails_verification(self):
        good = sign(transaction(self.user.id))
        tampered = transaction(self.user.id, total="1")
        self.assertEqual(self.post(tampered, signature=good).status_code, 401)

    def test_an_old_signature_is_rejected(self):
        body = transaction(self.user.id)
        stale = sign(body, ts=int(time.time()) - 3600)
        self.assertEqual(self.post(body, signature=stale).status_code, 401)

    def test_unfinished_transaction_grants_nothing(self):
        resp = self.post(transaction(self.user.id, status="paid"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_unrelated_event_is_acknowledged_but_ignored(self):
        body = transaction(self.user.id, event_type="customer.updated")
        self.assertEqual(self.post(body).status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_payload_without_a_user_is_a_bad_request_and_reported(self):
        from unittest.mock import patch

        with patch("resume.views.report_degraded") as report:
            self.assertEqual(self.post(transaction(None)).status_code, 400)
        report.assert_called_once()

    def test_an_unknown_price_grants_nothing(self):
        resp = self.post(transaction(self.user.id, price_id="pri_other"))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Purchase.objects.exists())

    def test_unknown_user_is_acknowledged_not_retried(self):
        """Returning an error would make the provider redeliver forever."""
        resp = self.post(transaction(999999))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Purchase.objects.exists())

    def test_non_json_body_is_a_bad_request(self):
        self.assertEqual(self.post(b"not json").status_code, 400)

    def test_redelivery_over_http_is_idempotent(self):
        body = transaction(self.user.id)
        self.post(body)
        self.post(body)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    @override_settings(PAYMENTS={**LIVE, "WEBHOOK_SECRET": ""})
    def test_missing_secret_refuses_everything(self):
        body = transaction(self.user.id)
        # Without a secret the account is not live, so the endpoint is closed.
        self.assertIn(self.post(body).status_code, (401, 503))
        self.assertFalse(Purchase.objects.exists())


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=LIVE)
class PricingPageTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x", email="ada@example.com")

    def test_pricing_is_public(self):
        resp = self.client.get(reverse("resume:pricing"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Pro · 3 months", html)
        self.assertIn("$24", html)
        self.assertIn("Create a free account to buy", html)
        # No checkout for a visitor: the payment must find an account.
        self.assertNotIn("paddle.js", html)

    def test_the_faq_explains_declined_cards(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("My card was declined", html)

    def test_the_free_plan_limits_are_on_the_page(self):
        from django.conf import settings

        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("AI credits", html)
        self.assertIn(str(settings.FREE_TIER_LIMITS["ai_credits"]), html)

    def test_a_signed_in_user_gets_a_paddle_checkout(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("cdn.paddle.com/paddle/v2/paddle.js", html)
        self.assertIn('data-price-id="pri_3m"', html)
        self.assertIn("test_client_token", html)
        self.assertIn(f'"user_id": "{self.user.id}"', html)

    def test_buyers_see_the_refund_terms_next_to_the_buy_button(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn('data-testid="purchase-consent"', html)
        self.assertIn(reverse("resume:refunds"), html)

    def test_a_plan_without_a_price_shows_as_unavailable(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("not set up yet", html)

    def test_return_from_checkout_says_payment_was_received(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume:pricing") + "?paid=1").content.decode()
        self.assertIn("Payment received", html)

    def test_pro_user_sees_their_expiry(self):
        self.client.force_login(self.user)
        self.user.profile.grant_premium(90)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("days left", html)

    def test_legal_pages_are_public_and_linked(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        for name in ("resume:terms", "resume:refunds"):
            self.assertIn(reverse(name), html)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertIn("Merchant of Record", self.client.get(reverse("resume:terms")).content.decode())
        refunds = self.client.get(reverse("resume:refunds")).content.decode()
        self.assertIn("14 days", refunds)
        # Refunds depend on use beyond the free plan, stated with the real limits.
        self.assertIn("30 AI credits", refunds)
        self.assertIn("5 PDF downloads", refunds)


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=LIVE)
class PurchasedAccessUnlocksFeaturesTest(TestCase):
    """A bought period must unlock the same things a staff-set tier does."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")

    def test_quotas_lift(self):
        profile = self.user.profile
        profile.import_count = 99
        profile.download_count = 99
        profile.save()
        self.assertFalse(profile.can_import())

        profile.grant_premium(90)
        self.assertTrue(profile.can_import())
        self.assertTrue(profile.can_download())
        self.assertTrue(profile.can_send_agent_message())
        self.assertTrue(profile.can_create_resume())


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=COMING_SOON)
class ComingSoonTest(TestCase):
    """Before Paddle approves the account, nothing may try to take money."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def test_is_live_is_false(self):
        self.assertFalse(payment_service.is_live())

    @override_settings(PAYMENTS={**LIVE, "CLIENT_TOKEN": ""})
    def test_live_without_keys_is_not_live(self):
        self.assertFalse(payment_service.is_live())

    def test_pricing_still_shows_the_plans_and_says_it_is_coming(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("Pro · 3 months", html)
        self.assertIn("$9", html)
        self.assertIn("not on sale yet", html)

    def test_no_checkout_is_offered(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertNotIn("paddle.js", html)
        self.assertIn(reverse("resume:register_premium_interest"), html)

    def test_webhook_is_closed(self):
        body = transaction(self.user.id)
        resp = self.client.post(
            reverse("resume:payment_webhook"), body,
            content_type="application/json", HTTP_PADDLE_SIGNATURE=sign(body),
        )
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(Purchase.objects.exists())

    def test_interest_is_recorded_against_the_plan(self):
        self.client.post(reverse("resume:register_premium_interest"), {"plan": "pro_12m"})
        entry = Feedback.objects.get()
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.page, "pricing")
        self.assertIn("12 months", entry.message)

    def test_unknown_plan_records_nothing(self):
        self.client.post(reverse("resume:register_premium_interest"), {"plan": "nope"})
        self.assertFalse(Feedback.objects.exists())

    def test_interest_requires_post_and_login(self):
        self.assertEqual(
            self.client.get(reverse("resume:register_premium_interest")).status_code, 405
        )
        self.client.logout()
        self.assertEqual(
            self.client.post(reverse("resume:register_premium_interest")).status_code, 302
        )

    def test_staff_can_still_grant_pro_by_hand(self):
        """Coming-soon gates payment, not access."""
        self.user.profile.tier = "pro"
        self.user.profile.save()
        self.assertTrue(self.user.profile.is_pro())


TEST_MODE = {**LIVE, "STATUS": "test"}


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=TEST_MODE)
class TestModeTest(TestCase):
    """A sandbox purchase on the live site that only staff can make."""

    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.staff = User.objects.create_user("owner", password="x", is_staff=True)

    def test_visitors_and_customers_still_see_coming_soon(self):
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertNotIn("paddle.js", html)
        self.client.force_login(self.user)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertNotIn("paddle.js", html)
        self.assertIn("not on sale yet", html)
        self.assertNotIn("Test mode", html)

    def test_staff_get_the_checkout_and_a_test_banner(self):
        self.client.force_login(self.staff)
        html = self.client.get(reverse("resume:pricing")).content.decode()
        self.assertIn("cdn.paddle.com/paddle/v2/paddle.js", html)
        self.assertIn("Test mode", html)

    def test_the_webhook_grants_access(self):
        body = transaction(self.staff.id)
        resp = self.client.post(
            reverse("resume:payment_webhook"), body,
            content_type="application/json", HTTP_PADDLE_SIGNATURE=sign(body),
        )
        self.assertEqual(resp.status_code, 200)
        self.staff.profile.refresh_from_db()
        self.assertTrue(self.staff.profile.is_pro())

    def test_it_is_not_live(self):
        self.assertFalse(payment_service.is_live())
        self.assertTrue(payment_service.is_test_mode())

    @override_settings(PAYMENTS={**TEST_MODE, "WEBHOOK_SECRET": ""})
    def test_without_keys_test_mode_is_off(self):
        self.assertFalse(payment_service.is_test_mode())
        self.assertFalse(payment_service.accepts_webhooks())


@override_settings(PREMIUM_PLANS=PLANS, PAYMENTS=LIVE)
class WebhookDiagnosticsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        self.url = reverse("resume:payment_webhook")

    def test_a_rejected_signature_is_reported_with_its_reason(self):
        from unittest.mock import patch

        body = transaction(self.user.id)
        with patch("resume.views.report_degraded") as report:
            resp = self.client.post(self.url, body, content_type="application/json",
                                    HTTP_PADDLE_SIGNATURE=sign(body, secret="wrong"))
        self.assertEqual(resp.status_code, 401)
        report.assert_called_once_with("payment_webhook_rejected", reason="signature_mismatch")

    def test_an_unsigned_stray_post_is_not_reported(self):
        from unittest.mock import patch

        with patch("resume.views.report_degraded") as report:
            resp = self.client.post(self.url, b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        report.assert_not_called()

    def test_any_of_several_signatures_may_match(self):
        body = transaction(self.user.id)
        good = sign(body)
        ts, h1 = good.split(";")
        rotated = f"{ts};h1=deadbeef;{h1}"
        resp = self.client.post(self.url, body, content_type="application/json",
                                HTTP_PADDLE_SIGNATURE=rotated)
        self.assertEqual(resp.status_code, 200)


class EnvKeyTest(TestCase):
    def test_quotes_and_whitespace_are_stripped(self):
        import os
        from unittest.mock import patch

        from core.settings import _env_key

        with patch.dict(os.environ, {"X_KEY": '  "pdl_ntfset_abc"\n'}):
            self.assertEqual(_env_key("X_KEY"), "pdl_ntfset_abc")
        with patch.dict(os.environ, {"X_KEY": "plain"}):
            self.assertEqual(_env_key("X_KEY"), "plain")
        self.assertEqual(_env_key("MISSING_KEY_FOR_TEST", "dflt"), "dflt")


class PlanLinkTest(TestCase):
    def test_signed_in_pages_link_to_pricing(self):
        user = User.objects.create_user("ada", password="x")
        self.client.force_login(user)
        html = self.client.get(reverse("resume:dashboard")).content.decode()
        self.assertIn(reverse("resume:pricing"), html)
        self.assertIn("Upgrade", html)

