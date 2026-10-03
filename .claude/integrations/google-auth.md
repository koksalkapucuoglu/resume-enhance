# Google sign-in (django-allauth) + email verification

**Status:** live · **Code:** `core/adapters.py`, `core/forms.py`, `core/middleware.py`, `core/email_verification.py`, `templates/socialaccount/`
**Env:** `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`

## Setup (Google Cloud console)

1. OAuth consent screen: external, app name ResuStack, scopes `email`, `profile`.
2. OAuth client (Web). Authorized redirect URI — exactly one per host:
   - prod: `https://resustackapp.com/accounts/google/login/callback/`
   - local: `http://localhost:8000/accounts/google/login/callback/`
3. Put the client id/secret in the environment. The Google button renders only
   when both are set (`GOOGLE_LOGIN_ENABLED`).

## Rules

- allauth is used **only for Google**. ResuStack's own login, sign-up and
  password views keep their paths; `include("allauth.urls")` is last in
  `core/urls.py`.
- Two auth backends → every `login(request, user)` passes `backend=`.
- **No matching on email.** `SOCIALACCOUNT_EMAIL_AUTHENTICATION` and
  `..._AUTO_CONNECT` stay `False` (local addresses were never verified;
  matching enables account takeover). Linking an existing account happens only
  from the Profile page (`process="connect"`).
- `SOCIALACCOUNT_AUTO_SIGNUP = False` forces `GoogleSignupForm` (transfer
  consent, email locked to Google's). Unverified Google emails are refused.
- Without credentials `GoogleLoginAvailabilityMiddleware` answers 404 for
  `/accounts/google/...` (allauth would 500 on `SocialApp.DoesNotExist`).
- `CanonicalHostMiddleware` redirects `www.` → bare domain; a sign-in started
  on www otherwise sends Google an unregistered callback.
- Failed/cancelled Google sign-ins redirect to login (or profile) with a
  message — never allauth's error page.

## Email verification (soft)

Email sign-ups get `UserProfile.email_verification_required = True` and a
signed, expiring link. While set, AI entry points answer with
`_ai_locked_message` — except the **first import**, which is allowed so a new
user sees their own CV in a design before checking their inbox
(`views._email_unverified(request, allow_first_import=True)`).
A new AI entry point must call `_email_unverified(request)` first.
Google and pre-existing accounts are never flagged.

Locally SMTP usually fails; generate a link with:

```bash
docker compose exec web python manage.py shell -c "from django.contrib.auth.models import User; from core.email_verification import make_token; from django.urls import reverse; print(reverse('verify_email', args=[make_token(User.objects.get(username='USERNAME'))]))"
```

Email transport itself: see `email.md`.
