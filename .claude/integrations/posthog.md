# PostHog — product analytics (pre-sales funnel)

**Status (2026-10-04):** code live, **key not set yet** — next step: create
the EU Cloud project, set `POSTHOG_API_KEY`, enable cookieless server hash
mode, then `check_integrations --send` and build the funnels below.
**Code:** `core/analytics.py` (`track`, `EVENTS`), `templates/partials/analytics.html` (cookieless web snippet), call sites in `resume/views.py`, `core/views.py`, `core/forms.py`, `resume/services/agent_tools.py`, `resume/services/evaluation_service.py`, `mcp_server/tools.py`
**Env:** `POSTHOG_API_KEY` (project key, public), `POSTHOG_HOST` (default `https://eu.i.posthog.com`)

## Design

| Half | Who | Identity | Storage on device |
|---|---|---|---|
| Web analytics | public pages: landing, pricing, sign-up, login, legal | none (cookieless daily hash) | **none** — no consent banner needed |
| Product events | signed-in accounts | `distinct_id = str(user.pk)` | none (server-side) |

The two halves are **not joined**: anonymous visitors cannot be followed into
their account. Accepted trade-off for a privacy policy that can say "no
cookies, no personal data". App pages load only Sentry, never the PostHog
snippet.

Event properties are enums, counts and flags. Never: username, email, names,
resume text, chat messages, postings, file names. Every event gets
`tier: free|pro`.

## Events (the contract — dashboards depend on these names)

| Event | Properties | Fired in |
|---|---|---|
| `signed_up` | `method: email|google` | `SignupView.post`, `GoogleSignupForm.save` |
| `email_verified` | — | `core.views.verify_email` |
| `resume_imported` | `linkedin: bool`, `flagged: int` | `views._save_import` |
| `resume_created` | `via: editor|chat|mcp` | `ResumeFormView.post`, `create_blank_resume` tool, MCP `create_resume` |
| `pdf_downloaded` | `via: editor|dashboard|link` | three download paths |
| `posting_evaluated` | `target: branch|base`, `score_band` | `views.evaluate_job_posting` |
| `improvement_applied` | `lines: int` | `views.improve_apply` |
| `chat_message_sent` | `has_active_resume: bool` | `agent_chat_stream` (charged turns) |
| `quota_reached` | `allowance: ai_credits|downloads|resumes|job_copies` | every quota refusal |
| `pricing_viewed` | `is_pro`, `live` | `pricing_page` (signed in) |
| `purchase_completed` | `plan`, `amount_cents`, `currency` | `payment_webhook` (first delivery only) |
| `ui_mode_changed` | `mode` | `toggle_agent_mode` |

Browser: `$pageview`/`$pageleave` on public pages; `checkout_opened {plan}` via
`window.resustackTrack` when the Paddle overlay opens.

Adding an event: add it to `core.analytics.EVENTS` (unknown names are dropped),
document it here, keep properties content-free.

## Funnels to build in PostHog

1. **Activation** (product events): `signed_up` → `resume_imported` OR
   `resume_created` → `pdf_downloaded`. Breakdown by `method`.
2. **Value** : `pdf_downloaded` → `posting_evaluated` → `improvement_applied`.
3. **Monetisation**: `quota_reached` → `pricing_viewed` → `purchase_completed`.
   Breakdown by `allowance` — tells which limit sells Pro.
4. **Top of funnel** (web analytics): pageviews `/` → `/pricing/` →
   `/accounts/signup/`; compare the `/accounts/signup/` pageviews with the
   `signed_up` count to see form abandonment.
5. **Email gate**: `signed_up{method=email}` → `email_verified` time-to-convert.

## Setup — owner's steps

1. posthog.com → sign up on **EU Cloud** (eu.posthog.com).
2. Project settings → copy the project API key (`phc_…`) → `POSTHOG_API_KEY`.
3. Project settings → Web analytics → enable **"Cookieless server hash mode"**
   (required by `cookieless_mode: "always"`).
4. Project settings → disable session replay, surveys, autocapture
   (the snippet already turns them off).
5. Data retention 12 months (the policy promises at most 12).
6. Dokploy env: `POSTHOG_API_KEY`. Redeploy. Build the funnels above.

Under `manage.py test` the key is blanked, so tests never send events; mock
`posthog.Posthog` to assert on calls (`resume/tests/test_observability.py`).
