# Handoff — where ResuStack stands and what comes next

Give this file (plus `CLAUDE.md`) to whoever continues. It says what is done,
what is waiting on the owner, and what to build next. Keep it current: when a
step finishes, move it to "Done" with the date.

Last updated: 2026-10-03

## Read first

1. `.claude/CLAUDE.md` — architecture, conventions, rules.
2. `.claude/integrations/README.md` — every external service, one file each.
3. `.claude/product/analysis-2026-10.md` — product review (pricing, modes,
   job evaluation, onboarding, MCP).
4. `.claude/product/feature-tree.md` — what exists vs. what a buyer needs.
5. `.claude/product/modes-analysis.md` — standard vs agentic: confusion points, parity gaps, recommendation.
6. `.claude/product/smoke-test.md` — every user-facing capability as a UI checklist.

Run locally: `docker compose up -d --build`, tests:
`docker compose exec web python manage.py test resume core mcp_server --parallel 4`.
Every push to `main` deploys to production.

## Done (October 2026, branch `presales-cleanup`)

- Removed: REST API v1 (resume CRUD; MCP is the integration), guided chat
  builder, `analyze/compare/find` chat tools, non-streaming agent endpoints,
  LinkedIn as a separate import, LemonSqueezy adapter, LaTeX renderer, legacy
  deploy files (Caddy, compose.prod, deploy.sh), stale docs.
- Free plan simplified to 4 allowances via `UserProfile.usage()`
  (AI credits 30/mo shared by import + enhance + chat + improvement drafts,
  PDFs 5/mo, resumes 3, job copies 3).
- One PDF import (LinkedIn detected from the text); first import allowed before
  email verification.
- Public pricing page with plan table + FAQ; Terms and Refund pages;
  sign-up returns to `?next=`.
- Paddle Billing: overlay checkout, signed webhook, idempotent grants.
- Sentry: unhandled errors + `report_degraded` kinds; error pages; 4xx/5xx
  cleaned (bad JSON → 400, unreadable PDF → 422, AI down → 503, renderer
  down → 503); enhance errors no longer written into the text box; model
  output escaped in the enhance fragment.
- PostHog: 12 server-side funnel events, cookieless page views on public
  pages.
- Privacy policies (EN + KVKK) list Paddle, Sentry, PostHog; version
  `2026-10-03`.
- Agentic chat history is stored per account in the browser (a new account on
  a shared browser used to see the previous person's chat).

- Agentic short-term fixes (2026-10-03): buttons run without the model (no
  credit), upload goes straight to a file picker with a staged progress bar
  (also on the start page), logo returns to the resume list, entering agentic
  starts a fresh chat, notices show in chat, template switch needs no
  approval, editor header is "← Dashboard / Ask AI", Edit opens in the same tab.
  Admin: JobPosting and Evaluation registered; "Mark email as verified"; a
  verified allauth EmailAddress lifts the AI lock.

## Waiting on the owner (accounts and secrets)

| What | Steps | Doc |
|---|---|---|
| Paddle account (Individual), domain review, products, token, webhook | then `PAYMENT_STATUS=live` | `integrations/payments.md` |
| Sentry org in **EU region**, DSN(s) | set `SENTRY_DSN` (+ browser DSN) | `integrations/sentry.md` |
| PostHog **EU Cloud** project, enable cookieless hash mode | set `POSTHOG_API_KEY` | `integrations/posthog.md` |
| Contact mailbox | `CONTACT_EMAIL` (defaults to privacy@resustackapp.com) | — |
| Have Terms / Refund / Privacy read by a lawyer or mali müşavir | — | `/terms/`, `/refunds/` |
| MCP Registry republish (server.json 2.0.0) | `mcp-publisher publish` | `integrations/mcp.md` |

## Next — candidates, in suggested order

1. **Job evaluation in the standard editor** — the paid workflow is reachable
   only in chat today. A "Score against a posting" panel in the editor's right
   pane reusing `evaluation_service.panel` + the improve cards.
2. **First-run path** — after sign-up land on one screen: "Import your PDF"
   (big) / "Start blank" (small); after import show the design picker, then
   the posting score. Measure with the activation funnel.
3. **Refund automation** — Paddle `adjustment.updated` (refund approved) →
   shorten `premium_until`.
4. **Cover letter for a posting** — one OpenAI draft from resume + posting,
   checked by Jev for unsupported claims like improvement drafts; costs 1 AI credit.
5. **MCP OAuth 2.1** — lets claude.ai users connect without a token.
6. **Async AI calls** (imports, evaluations) if PostHog/Sentry show timeouts.

Each candidate: write the plan here first, then build on a branch, full suite,
then push.
