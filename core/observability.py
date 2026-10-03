"""
Error tracking (Sentry) for what breaks a person's flow.

Sentry is used as an error tracker, not a logger: the plan has an event quota,
and a log line per request would spend it on noise. Two things reach it:

- unhandled exceptions (the Django integration sends them; every one of them
  is a 500 somebody saw), and
- `report_degraded(...)`: a flow that failed for the user *without* an
  exception — the AI did not answer, its answer could not be read, a PDF
  could not be rendered, a verification email did not go out.

Neither carries user content. Events hold ids, error types and tags, the same
rule the logs follow (see the privacy policy).
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Keys that may hold resume text, chat messages, postings or credentials.
# Scrubbed from request data and extras before an event leaves the server.
_SENSITIVE_KEYS = {
    "message", "messages", "history", "content", "text", "posting", "cv_file",
    "linkedin_file", "password", "password1", "password2", "old_password",
    "new_password1", "new_password2", "token", "email", "description", "notes",
    "instruction", "rewrites", "lines", "csrfmiddlewaretoken",
}


def init():
    """Start Sentry when a DSN is configured. Without one this is a no-op."""
    dsn = getattr(settings, "SENTRY_DSN", "")
    if not dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=getattr(settings, "SENTRY_ENVIRONMENT", "production"),
        release=getattr(settings, "SENTRY_RELEASE", None) or None,
        integrations=[
            DjangoIntegration(),
            # Log records stay breadcrumbs; only exceptions become events.
            LoggingIntegration(level=logging.INFO, event_level=None),
        ],
        # Errors only: no performance tracing, no profiling, no replays.
        traces_sample_rate=0.0,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=_before_send,
    )
    return True


def _scrub(value):
    if isinstance(value, dict):
        return {
            k: ("[scrubbed]" if str(k).lower() in _SENSITIVE_KEYS else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _before_send(event, hint):
    request = event.get("request") or {}
    request.pop("data", None)
    request.pop("cookies", None)
    if "query_string" in request:
        request["query_string"] = ""
    headers = request.get("headers") or {}
    for key in list(headers):
        if key.lower() in ("cookie", "authorization", "x-csrftoken"):
            headers[key] = "[scrubbed]"
    if "extra" in event:
        event["extra"] = _scrub(event["extra"])
    # Frame locals can hold resume text; keep the stack, drop the values.
    for exception in (event.get("exception") or {}).get("values", []):
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)
    return event


def report_degraded(kind, **tags):
    """
    A user-facing flow failed without raising.

    `kind` is a stable slug (it is also the Sentry fingerprint, so repeats
    group into one issue); `tags` are short, content-free facts such as an
    error type or a flag. Always logged; sent to Sentry when it is on.
    """
    logger.warning("Degraded flow: %s %s", kind, tags)
    try:
        import sentry_sdk
    except ImportError:
        return
    if not sentry_sdk.get_client().is_active():
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("degraded", kind)
        for key, value in tags.items():
            scope.set_tag(key, str(value)[:200])
        scope.fingerprint = ["degraded", kind]
        sentry_sdk.capture_message(f"Degraded: {kind}", level="warning")


def report_exception(exc, **tags):
    """
    An exception the code caught to keep the page alive (the user still saw
    an error). Unhandled ones reach Sentry on their own; these would not.
    """
    try:
        import sentry_sdk
    except ImportError:
        return
    if not sentry_sdk.get_client().is_active():
        return
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, str(value)[:200])
        sentry_sdk.capture_exception(exc)
