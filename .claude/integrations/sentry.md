# Sentry — error tracking

**Status (2026-10-04):** live — EU org, server + browser DSNs set in Dokploy;
`check_integrations --send` event received. Alert rules created: "new issue →
email" and "tag `degraded` starts with `payment_` → email" (5 min throttle).
**Code:** `core/observability.py` (init, scrubbing, `report_degraded`, `report_exception`), `core/wsgi.py` (init), `templates/partials/analytics.html` (browser SDK), `core/views.py` (error pages)
**Env:** `SENTRY_DSN` (server), `SENTRY_BROWSER_DSN` (browser, optional, can be the same project), `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`

## Principle

An **error tracker, not a logger** — the plan has an event quota. Only two
things become events:

1. **Unhandled exceptions** (Django integration). Every one is a 500 a person
   saw, so every one is a bug to fix.
2. **`report_degraded(kind, **tags)`** — a flow that failed for the person
   *without* an exception. `kind` is the fingerprint, so repeats group into
   one issue. Current kinds:

| kind | where | means |
|---|---|---|
| `import_ai_unavailable` | `views.upload_cv` | OpenAI did not answer an import (503 to user) |
| `import_unparseable_output` | `views.upload_cv` | model answer was not JSON (502) |
| `enhance_ai_unavailable` | `views.enhance_field` | bullet rewrite failed (503, text untouched) |
| `agent_llm_failure` | `views._agent_response` | chat turn failed |
| `evaluation_unavailable` | `evaluation_service` | Jev did not answer; no score (503) |
| `pdf_render_failed` | editor / dashboard / signed link | WeasyPrint failed (503) |
| `verification_email_failed` | `core/email_verification.py` | SMTP refused |
| `payment_webhook_rejected` (tag `reason`: signature_mismatch, stale_timestamp, no_signature, no_secret) / `payment_webhook_unusable` / `payment_not_granted` / `payment_for_unknown_user` | `views.payment_webhook` | **money may be taken without access — act immediately** |

`report_exception(exc, **tags)` is for exceptions the code catches to keep a
page alive (agent stream, MCP dispatcher).

Log records are breadcrumbs only (`event_level=None`); `logger.error` does not
spend quota. No tracing, profiling or replays (`traces_sample_rate=0`).

## Privacy

`_before_send` drops request bodies, cookies, query strings, auth/CSRF headers,
scrubs sensitive keys in extras (`message`, `content`, `text`, `posting`,
`email`, passwords, tokens…) and removes frame locals. `send_default_pii=False`.
Tags must never carry user content — ids, types, slugs only.
Sentry is a named processor in both privacy policies and the sign-up consent.
**Create the organisation in the EU data region** — the policy says so.

## Setup — owner's steps

1. sentry.io → create organisation, **data region: EU**.
2. Create project "resustack" (platform Django). Copy the DSN → `SENTRY_DSN`.
3. Optional: a second project "resustack-browser" (platform Browser JavaScript)
   → `SENTRY_BROWSER_DSN`. Allowed domains: `resustackapp.com`.
4. Dokploy env: `SENTRY_DSN`, `SENTRY_BROWSER_DSN`, `SENTRY_ENVIRONMENT=production`.
   Optionally `SENTRY_RELEASE=<git sha>`.
5. Alerts: one rule "new issue → email", and one for `degraded:payment_*` →
   email immediately.
6. Data retention: 90 days (the policy promises at most 90).

## Error pages and status codes

`handler400/403/404/500` → `core.views` render `templates/error.html` (what
happened, what to do), or JSON `{"error", "status"}` for script requests
(`X-Requested-With`, JSON `Accept`/body, `/agent/`, `/mcp`, `/webhooks/`).

Status rules used across views:
- 400 the request was wrong (bad JSON body → always 400, never 500)
- 403 not allowed: quota (`quota_exceeded: true`), email not verified
- 404 not yours / not there (`user=request.user` filters)
- 422 the file is unusable (not a PDF, no text)
- 502 the AI answered something we cannot read
- 503 a dependency is down (OpenAI, Jev, WeasyPrint) — `_service_error_status(exc)`
  maps `EvaluationUnavailable` to 503
- 500 only for real bugs — each reaches Sentry
