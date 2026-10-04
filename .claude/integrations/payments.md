# Payments — merchant of record (Paddle adapter; provider decision open)

**Status (2026-10-04):** Paddle **sandbox** works end to end on production in
`PAYMENT_STATUS=test` (staff-only checkout → signed webhook → `Purchase` →
Pro). Paddle **live was rejected** at domain review: "Other/Resume/CV
Builders" is a category outside Paddle's Acceptable Use Policy. An appeal was
sent (text below). **Next:** apply to Dodo Payments and, if accepted, add a
`DodoProvider` next to `PaddleProvider` (see "Switching provider").
**Code:** `resume/services/payment_service.py`, `views.pricing_page`, `views.payment_webhook`, `resume/templates/resume/pricing.html`, `Purchase` + `UserProfile.premium_until`
**Env:** `PAYMENT_STATUS`, `PADDLE_ENVIRONMENT`, `PADDLE_CLIENT_TOKEN`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_PRO_3M`, `PADDLE_PRICE_PRO_12M`, `CONTACT_EMAIL`

## Rejection, appeal and alternatives

- Paddle's reason is the category (resume builders' free-build → paywall at
  download → hidden subscription pattern causes chargebacks), not our site.
  The appeal argued our model is the opposite: free plan includes 5 PDF
  downloads/month, one-time purchase, no renewal, no converting trial, prices
  public before sign-up, software only, no fake credentials or impersonation.
- **Dodo Payments**: lists "Resume, hiring, or exam tools" under *categories
  that require review* (condition: no impersonation or cheating features) —
  not prohibited. Accepts individuals by government ID; Türkiye eligible.
  Recommended next provider.
- **Polar**: same "requires review" listing; payouts via Stripe Connect
  Express — whether a Türkiye individual can onboard is unclear.
- **Lemon Squeezy**: new merchants invite-gated; prohibits service-type
  products. Not pursued.
- **iyzico / PayTR**: category fine, but need a tax registration (şahıs
  şirketi) and you remit KDV yourself.
- Live Paddle objects already created (product `pro_01m42zgngbxpzjhcyma5h4qz5y`,
  prices `pri_01m42zhrjc6e432vnedx7rs45r` / `pri_01m42zjdyrdk7kpdbe9xhp90z4`,
  webhook, client token `resustack-web`) — unused unless the appeal succeeds.
- Answers given in Paddle's forms (product overview, delivery): software
  only, delivered as a web app, Pro activates automatically on payment, PDFs
  downloaded in-app. Reuse for the next provider's application.

## Model

One-time purchase of a period, no subscription: Pro 3 months $9, Pro 12 months
$24. Buying again extends from the later of now / current expiry. When it ends
the account is free again and nothing is deleted. `is_pro()` is true for
`tier == "pro"` (staff grant, no expiry) or `premium_until` in the future.

## Why Paddle (decided October 2026)

The seller is an individual in Türkiye with no company.

| Option | Individual from TR? | Notes |
|---|---|---|
| **Paddle** | yes ("Individual" business type) | MoR: issues invoices, collects VAT/KDV, handles refunds/chargebacks. 5% + $0.50. Monthly payout (≥ $100) by SWIFT ($15) in USD/EUR/GBP. Reviews the website (pricing, terms, refund policy must be public). |
| Dodo Payments | yes (eligibility by ID document) | MoR, 4% + $0.40. Good fallback if Paddle rejects. |
| Polar.sh | depends on Stripe Connect Express "individual" in TR | MoR on Stripe; uncertain for TR individuals. |
| LemonSqueezy | signups invite-gated since mid-2026, being folded into Stripe Managed Payments | the previous adapter; removed. |
| Gumroad | no TR payouts | — |
| iyzico / PayTR / Shopier | need a tax registration (şahıs şirketi) | not MoR — you file KDV yourself. Revisit only for a TR-only, TRY-priced offer. |

Tax note (not advice — confirm with a mali müşavir): income from a MoR is
foreign-sourced service income; the 2026 decree exempts service exports, but
regular sales still create a tax registration duty in Türkiye.

## Flow

```
/pricing/ (public)
  visitor → "Create a free account to buy" → /accounts/signup/?next=/pricing/
  signed-in → Paddle.js overlay: items=[{priceId}], customer.email,
              customData={user_id}, successUrl=/pricing/?paid=1
Paddle → POST /webhooks/payments/  (transaction.completed)
  verify Paddle-Signature (ts=…;h1=HMAC-SHA256(secret, "ts:" + raw body), 300 s window)
  plan ← items[0].price.id  (never from client data)
  user ← custom_data.user_id
  record_purchase(): idempotent on txn id → grant_premium(days)
```

Answers: 200 granted / ignored event / unknown user (no retry loop);
400 bad payload (reported to Sentry — money may have been taken); 401 bad
signature; 503 while not live.

## Going live — owner's steps

1. Sign up at paddle.com as **Individual**; complete identity verification and
   payout details (bank SWIFT, USD).
2. Add the domain `resustackapp.com` for review. Paddle checks: public
   `/pricing/`, `/terms/`, `/refunds/`, `/privacy/`, contact email.
3. Catalog → Products: "ResuStack Pro" with two **one-time** prices: $9
   ("3 months") and $24 ("12 months"). Copy the `pri_…` ids.
4. Developer tools → Authentication → **client-side token** (`live_…`).
5. Developer tools → Notifications → destination
   `https://resustackapp.com/webhooks/payments/`, event `transaction.completed`.
   Copy its **secret key**.
6. Dokploy env: `PADDLE_ENVIRONMENT=production`, `PADDLE_CLIENT_TOKEN`,
   `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_PRO_3M`, `PADDLE_PRICE_PRO_12M`,
   then `PAYMENT_STATUS=live`. Redeploy.
7. Before that, test in the **sandbox** on the live site: sandbox keys +
   `PADDLE_ENVIRONMENT=sandbox` + `PAYMENT_STATUS=test`. Only staff accounts
   (`is_staff`) see the buy button; everyone else still sees "not on sale
   yet", and the webhook accepts the sandbox purchase. Buy once with
   `4242 4242 4242 4242`, then go back to `coming_soon`.

`PAYMENT_STATUS`: `coming_soon` (default: no checkout, webhook 503) · `test`
(checkout for staff only, webhook open) · `live` (checkout for everyone).

## Refunds

Policy (`/refunds/`): full refund within 14 days **if Pro was not used beyond the free plan's limits** (30 AI credits, 5 PDF downloads since purchase); beyond that no refund except a problem we cannot fix, a duplicate purchase, or where the law requires it. Check usage in admin (User profiles → counters) before approving. The pricing page shows a consent line under the buy buttons (immediate start, refund terms). Refunds are issued in the
Paddle dashboard. Access is not revoked automatically yet: after a refund set
the user's `premium_until` back in Django admin. (Automating it = handle
`adjustment.updated` with `action=refund`, `status=approved`.)

## Waitlist (until live)

While `coming_soon`, signed-in users can press "Tell me when it's ready"; it
stores `Feedback(page="pricing", message="Waitlist: <plan>")`.

```bash
docker compose exec web python manage.py shell -c "from resume.models import Feedback; [print(f.created_at, f.user, f.message) for f in Feedback.objects.filter(page='pricing')]"
```

## Privacy

Paddle is a named processor in both privacy policies (billing data is
collected by Paddle, not by ResuStack).
