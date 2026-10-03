# Email (SMTP)

**Status:** live · **Code:** `core/settings.py` (EMAIL_*), `core/email_verification.py`, password reset (Django built-in)
**Env:** `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, optional `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`

Defaults are Gmail (`smtp.gmail.com:587`, TLS) with an app password.
For a provider on port 465 (implicit TLS): `EMAIL_PORT=465`,
`EMAIL_USE_TLS=False`, `EMAIL_USE_SSL=True`.

Sent mail: verification link, password reset. Nothing else (no marketing).

Production must have real credentials: email verification locks AI features
(after the first import) until the link arrives. A failed send is logged
(`Verification email could not be sent for user <id>`) and reported to Sentry
as degraded — the user can resend from the banner.
