"""
Which external services are configured, and — with --send — whether they answer.

    python manage.py check_integrations          # configuration only, nothing sent
    python manage.py check_integrations --send   # also one test event to Sentry and PostHog

Prints variable names and whether they are set, never their values.
"""

from django.conf import settings
from django.core.management.base import BaseCommand


def _set(value):
    return "set" if value else "MISSING"


class Command(BaseCommand):
    help = "Report which integrations are configured; --send fires one test event each."

    def add_arguments(self, parser):
        parser.add_argument("--send", action="store_true", help="Send a test event to Sentry and PostHog.")

    def handle(self, *args, **options):
        out = self.stdout.write

        out("OpenAI")
        out(f"  OPENAI_API_KEY            {_set(settings.OPENAI_API_KEY)}")
        out("TypeSafe (Jev)")
        out(f"  TYPESAFE_API_KEY          {_set(settings.TYPESAFE_API_KEY)}  model={settings.TYPESAFE_MODEL}")
        out("Google sign-in")
        out(f"  GOOGLE_LOGIN_ENABLED      {settings.GOOGLE_LOGIN_ENABLED}")

        from resume.services import payment_service

        payments = settings.PAYMENTS
        out("Paddle")
        out(f"  PAYMENT_STATUS            {payments.get('STATUS')}")
        out(f"  PADDLE_ENVIRONMENT        {payments.get('ENVIRONMENT')}")
        out(f"  PADDLE_CLIENT_TOKEN       {_set(payments.get('CLIENT_TOKEN'))}")
        out(f"  PADDLE_WEBHOOK_SECRET     {_set(payments.get('WEBHOOK_SECRET'))}")
        for plan in settings.PREMIUM_PLANS:
            out(f"  price {plan['code']:<18}{_set(plan.get('price_id'))}")
        out(f"  checkout for everyone     {payment_service.is_live()}")
        out(f"  test mode (staff only)    {payment_service.is_test_mode()}")
        out(f"  webhook accepts payments  {payment_service.accepts_webhooks()}")

        out("Sentry")
        out(f"  SENTRY_DSN                {_set(settings.SENTRY_DSN)}  env={settings.SENTRY_ENVIRONMENT}")
        out(f"  SENTRY_BROWSER_DSN        {_set(settings.SENTRY_BROWSER_DSN)}")
        out("PostHog")
        out(f"  POSTHOG_API_KEY           {_set(settings.POSTHOG_API_KEY)}  host={settings.POSTHOG_HOST}")
        out(f"CONTACT_EMAIL               {settings.CONTACT_EMAIL}")

        if not options["send"]:
            return

        if settings.SENTRY_DSN:
            import sentry_sdk

            from core import observability

            observability.init()
            event_id = sentry_sdk.capture_message("ResuStack integration check", level="info")
            sentry_sdk.flush(timeout=10)
            out(self.style.SUCCESS(f"Sentry: test message sent (event {event_id}). Look for it under Issues."))
        else:
            out(self.style.WARNING("Sentry: skipped, no SENTRY_DSN."))

        if settings.POSTHOG_API_KEY:
            from posthog import Posthog

            client = Posthog(settings.POSTHOG_API_KEY, host=settings.POSTHOG_HOST, sync_mode=True)
            client.capture("integration_check", distinct_id="integration-check", properties={"source": "manage.py"})
            client.shutdown()
            out(self.style.SUCCESS("PostHog: 'integration_check' event sent. Look for it under Activity."))
        else:
            out(self.style.WARNING("PostHog: skipped, no POSTHOG_API_KEY."))
