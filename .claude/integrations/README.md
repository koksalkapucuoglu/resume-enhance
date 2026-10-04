# Integrations

One file per external service: what it does here, where the code is, which
environment variables it reads, the rules for touching it, and the owner's
setup steps. Values never live in these files — only variable names. Secrets
are set in Dokploy (production) and `.env` (local, git-ignored).

| Integration | Status | Doc | Env |
|---|---|---|---|
| OpenAI (text generation) | live | [openai.md](openai.md) | `OPENAI_API_KEY` |
| TypeSafe / Jev (typed judgments, scores) | live | [typesafe.md](typesafe.md) | `TYPESAFE_API_KEY`, `TYPESAFE_MODEL`, `TYPESAFE_TIMEOUT` |
| Google sign-in + email verification | live | [google-auth.md](google-auth.md) | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` |
| Email (SMTP) | live | [email.md](email.md) | `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `EMAIL_*` |
| MCP server + MCP Registry | live | [mcp.md](mcp.md) | `MCP_REGISTRY_AUTH` |
| Payments, merchant of record (Paddle adapter) | sandbox works (`PAYMENT_STATUS=test`); Paddle live **rejected** (CV-builder category), appeal sent; next: Dodo Payments | [payments.md](payments.md) | `PAYMENT_STATUS`, `PADDLE_*`, `CONTACT_EMAIL` |
| Sentry (error tracking) | live, alerts set | [sentry.md](sentry.md) | `SENTRY_DSN`, `SENTRY_BROWSER_DSN`, `SENTRY_ENVIRONMENT` |
| PostHog (product analytics) | code live, key not set yet | [posthog.md](posthog.md) | `POSTHOG_API_KEY`, `POSTHOG_HOST` |
| Dokploy + Cloudflare (hosting) | live | [deployment.md](deployment.md) | `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `POSTGRES_*`, `CACHE_LOCATION` |

## Checking what is configured

```bash
docker compose exec web python manage.py check_integrations          # set / MISSING per variable, values never printed
docker compose exec web python manage.py check_integrations --send   # plus one test event to Sentry and PostHog
```

In production run it from the Dokploy container terminal.

## Rules that apply to every integration

- **Optional by default.** Code must run with the variable unset: the feature
  turns off (Sentry, PostHog), shows "coming soon" (Paddle) or fails open
  (Jev, except scoring). Tests run without any keys.
- **A new processor of user data** → update `privacy.html` and
  `privacy_tr.html`, the consent text in both sign-up templates, bump
  `PRIVACY_POLICY_VERSION`, add it to `PROCESSORS` in
  `resume/tests/test_privacy_and_registry.py`, and add a row here.
- **No content in logs, Sentry events or analytics** — ids, counts, types.
- Add new variables to `.env.example` with a one-line comment.
- A new external script or CDN is a processor too (it sees the visitor's IP).
