# Pre-sales feature tree

What a person needs to go from "never heard of it" to "paid", and where
ResuStack stands. ✅ have · 🟡 partial / works with caveats · ❌ missing ·
✂️ removed on purpose (October 2026 cleanup).

Rule used for every branch: **the simplest version that does the job**. A
feature is kept only if a first-time user would miss it.

```
ResuStack
├── 1. Discover & trust
│   ├── ✅ Landing page — honest claims, design gallery, Claude/MCP section
│   ├── ✅ Public pricing page with a Free / Pro table and FAQ   (was login-only)
│   ├── ✅ Terms of Service, Refund Policy (14 days)             (new)
│   ├── ✅ Privacy policy EN + KVKK TR, named processors
│   ├── ✅ Contact (CONTACT_EMAIL)
│   └── ❌ Examples / social proof — real before/after PDFs (later, only with consent)
│
├── 2. Sign up
│   ├── ✅ Email + password, Google
│   ├── ✅ Explicit consent to transfers abroad (KVKK)
│   ├── ✅ Return to where you came from (?next=, e.g. pricing)  (new)
│   └── 🟡 Email verification — no longer blocks the first import (new);
│          chat/enhance still wait for the link
│
├── 3. First resume  ← activation moment
│   ├── ✅ One "Import PDF" — CV or LinkedIn export, detected automatically (merged)
│   ├── ✅ Import check: flags values the PDF does not support
│   ├── ✅ Start blank in the form editor
│   ├── ✅ Start blank in chat: creates a real resume, fills it turn by turn (new)
│   └── ✂️ Guided question-by-question builder (duplicated the chat)
│
├── 4. Edit
│   ├── ✅ Form editor + live preview = the PDF
│   ├── ✅ 14 designs, ATS-safe marked
│   ├── ✅ AI rewrite of a bullet (errors now explained, text never overwritten by an error)
│   ├── ✅ Chat editing (agentic mode) with approvals and undo
│   ├── ✅ History: every save/AI edit restorable, word diff — free on every plan
│   ├── ✅ Language versions EN/TR
│   ├── ✅ "What I'm working on" section
│   └── ✂️ analyze / compare / find in chat (scored with OpenAI, overlapped with posting score)
│
├── 5. Target a job  ← what Pro is for
│   ├── ✅ Paste a posting → requirement-by-requirement score (Jev, same yardstick)
│   ├── ✅ Job copy (branch) per posting; main resume untouched
│   ├── ✅ Improve selected gaps → reviewed drafts → apply → re-score
│   ├── 🟡 Only in agentic mode and over MCP — not in the standard editor
│   └── ❌ Cover letter for a posting
│
├── 6. Export
│   ├── ✅ PDF (WeasyPrint), signed one-time links for MCP
│   └── ❌ DOCX
│
├── 7. Plans & payment
│   ├── ✅ Free: 30 AI credits/mo, 5 PDFs/mo, 3 resumes, 3 job copies  (was 6 counters)
│   ├── ✅ Pro: one-time $9 / 3 months, $24 / 12 months, no renewal
│   ├── ✅ Paddle checkout (merchant of record, TRY display, invoices, VAT/KDV) (new, awaiting account)
│   ├── ✅ Usage shown the same everywhere (profile, chat, MCP) via UserProfile.usage()
│   ├── 🟡 Refund → access revoked by hand in admin
│   └── ✂️ LemonSqueezy adapter, hosted-checkout redirect
│
├── 8. Use from Claude (MCP)
│   ├── ✅ 11 tools, registry listing, bearer token
│   ├── ❌ OAuth (needed for claude.ai custom connectors)
│   └── ✂️ REST API v1 (no client; MCP is the integration)
│
└── 9. Operate
    ├── ✅ Error pages for 400/403/404/500; JSON for scripts       (new)
    ├── ✅ Sentry: unhandled errors + "degraded" flows, content-free (new)
    ├── ✅ PostHog: funnel events (server) + cookieless page views   (new)
    ├── ✅ Feedback widget, waitlist until payments are live
    └── 🟡 AI calls are synchronous (5–30 s) — fine at current volume
```

## Simplifications made

| Before | After | Why |
|---|---|---|
| 6 counters (imports, enhancements, chat messages, downloads, resumes, branches) | 4: AI credits, downloads, resumes, job copies | one sentence explains the free plan |
| 10 chat messages / month | 30 AI credits shared | chat is the headline; 10 ran out during onboarding |
| "Import PDF" + "Import LinkedIn" | one import | the person only knows "I have a PDF" |
| guided builder + agent | agent only | two assistants answered the same message |
| 24 chat tools | 20 | fewer overlaps, fewer wrong tool picks |
| pricing behind login | public | price before sign-up; required by Paddle |
| REST API + MCP | MCP | one integration surface, less to secure |

## Not done on purpose (still "later")

Job evaluation in the standard editor, cover letters, DOCX, MCP OAuth,
async AI calls — see `analysis-2026-10.md` and the discussion in `HANDOFF.md`.
