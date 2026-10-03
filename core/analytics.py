"""
Product analytics (PostHog): where people get stuck before they buy.

Two halves, both free of personal data:

- **Server-side events** for the product funnel (signed up → imported →
  downloaded → scored a posting → opened pricing → bought). The distinct id is
  the account's numeric id — pseudonymous; no username, email, resume text,
  chat or posting ever goes into an event. Properties are small enums,
  counts and flags.
- **Web analytics on public pages** (`templates/partials/analytics.html`):
  posthog-js in cookieless mode, so nothing is stored on the visitor's device
  and no consent banner is needed. Those visitors stay anonymous; they are not
  joined to the server-side ids.

Off without `POSTHOG_API_KEY`. Never raises: analytics must not break a flow.
The event names are the contract with the PostHog dashboards — see
`.claude/integrations/posthog.md` before renaming one.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Events the server sends. Adding one: name it here, document it in
# .claude/integrations/posthog.md, keep its properties content-free.
EVENTS = {
    "signed_up",            # method: email | google
    "email_verified",
    "resume_imported",      # linkedin: bool, flagged: int
    "resume_created",       # via: editor | chat | mcp
    "pdf_downloaded",       # via: editor | dashboard | chat | link
    "posting_evaluated",    # target: branch | base, score_band: 0-39 | 40-69 | 70-100
    "improvement_applied",  # lines: int
    "chat_message_sent",    # has_active_resume: bool
    "quota_reached",        # allowance: ai_credits | downloads | resumes | job_copies
    "pricing_viewed",       # is_pro: bool
    "purchase_completed",   # plan, amount_cents, currency
    "ui_mode_changed",      # mode: standard | agentic
}

_client = None


def _get_client():
    global _client
    key = getattr(settings, "POSTHOG_API_KEY", "")
    if not key:
        return None
    if _client is None:
        from posthog import Posthog

        _client = Posthog(
            key,
            host=settings.POSTHOG_HOST,
            disable_geoip=True,
            # Events are queued and sent by a background thread.
            flush_interval=5.0,
        )
    return _client


def track(user, event, **properties):
    """Record one product event for an account. Silent no-op when off."""
    if event not in EVENTS:
        logger.warning("Unknown analytics event %r", event)
        return
    user_id = getattr(user, "pk", None) if user is not None else None
    if not user_id:
        return
    client = _get_client()
    if client is None:
        return
    try:
        props = dict(properties)
        try:
            props["tier"] = "pro" if user.profile.is_pro() else "free"
        except Exception:  # noqa: BLE001 — a profile lookup must not cost the event
            pass
        client.capture(event, distinct_id=str(user_id), properties=props)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Analytics capture failed: %s", type(exc).__name__)


def score_band(score):
    """A coarse band: the exact score is not needed to see where people stop."""
    if score is None:
        return "none"
    if score < 40:
        return "0-39"
    if score < 70:
        return "40-69"
    return "70-100"
