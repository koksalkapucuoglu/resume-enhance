# Product analysis — October 2026

Snapshot taken before the pre-sales cleanup (branch `presales-cleanup`).
Method: ran the app locally, imported five real-world CVs (EN + TR, a
LinkedIn-style export, two Turkish template CVs), signed up as a brand-new
user, used both modes, read the code paths behind each feature.
Baseline: 614 tests passing.

## Verdict in one paragraph

The engine is strong and honest — import that flags what the PDF does not
support, a preview that *is* the PDF, undo on every AI edit, and a job-posting
measurement that uses the same yardstick every time. What stands between it
and a sale is the shell around that engine: the price is behind a login, the
free plan has six different counters, the best feature (job evaluation) only
exists inside a chat, a new user's first AI action is locked behind an email,
and nothing tells us where people drop off.

## Pricing

| | Today | Problem |
|---|---|---|
| Model | One-time $9 / 3 months, $24 / 12 months | Fine — matches episodic job search. Keep. |
| Sale status | `coming_soon`, waitlist via `Feedback` | Nothing can be bought. |
| Provider | LemonSqueezy adapter | LemonSqueezy signups are invite-gated (mid-2026) and the product is being folded into Stripe Managed Payments. Not a safe bet. |
| Free plan | 6 counters: imports 2/mo, enhancements 10/mo, downloads 5/mo, resumes 3, job branches 3, chat messages 10/mo | Nobody can hold six numbers in their head. 10 chat messages a month is exhausted during onboarding — and chat is the headline feature. |
| Pricing page | Login required; no Free-vs-Pro table | A visitor cannot see the price before signing up. Payment providers also review the public pricing page. |
| Legal pages | Privacy (EN) + KVKK (TR) | No Terms of Service, no refund policy — both are required by every merchant of record. |

## Standard mode

Strong: split editor with a live preview that matches the PDF, 14 designs,
import flags in the form, history modal with word diff, language versions,
"What I'm working on" section.

Weak:
- Job evaluation — the paid workflow — is not reachable here. Branches are
  listed under their base, but you cannot paste a posting.
- Empty dashboard offers only "Create New Resume"; import is one page deeper.
- PDF import and LinkedIn import are two separate entry points for what the
  user sees as one action ("I have a PDF").

## Agentic mode

Strong: native tool calling, streaming, approval cards, Jev guardrail, the
posting → evaluate → improve → apply flow with unsupported-claim checks.

Weak / bugs found while testing:
- **Privacy bug:** chat history is kept in `localStorage` under a fixed key
  (`resustack_chat`) — the next person to sign in on the same browser sees the
  previous person's conversation. A new account opened on a shared machine
  showed someone else's job-posting chat.
- Guided builder (a fixed question state machine) runs alongside the agent:
  asking to "build from scratch" produced two assistant questions in a row.
- 24 tools, several overlapping (`translate_resume` vs `create_translated_copy`,
  `find_resume`, `compare_resumes`, `analyze_resume` scoring with OpenAI while
  postings are scored with Jev).
- New users land here by default with an empty list and an "Application Score"
  chip they cannot use yet.
- Non-streaming `agent/chat/` and `agent/approve/` endpoints are no longer
  called by the UI.

## Job-posting evaluation

The differentiator. Parsed once, measured by Jev, evidence location per
requirement, a branch per posting, improvement drafts checked against what the
person actually said. Available in agentic mode and over MCP.

Issues: hidden behind chat; "branch" and "promote" are git words; no Jev → no
evaluation (by design, but it must be reported, not silent).

## New-user friendliness

| Step | Finding |
|---|---|
| Landing | Clear, no inflated claims. "Pricing" in the nav sends a visitor to a login page. |
| Sign-up | Username + email + two passwords + consent. Google is optional. Login page `<title>` is Turkish on an English page. |
| First AI action | Locked until the verification email is clicked. The first thing a new user wants — "import my CV" — is the thing that is locked. |
| First screen | Agentic chat with an empty list; standard mode would be more predictable for a first visit. |
| Messages | Notices queued while in agentic mode surface later in standard mode, sometimes contradicting each other ("could not send" + "confirmed"). |

## MCP

11 tools, listed in the official registry, bearer-token auth. Good marketing
for a developer audience and costs little to keep. claude.ai custom connectors
expect OAuth, so most non-developers cannot connect yet. Keep as is; OAuth is
the only thing that would widen it.

## Other

- DRF `ResumeViewSet` ("mobile API") has no client — only the feedback
  endpoint under `/api/v1/` is used. Dead surface that still accepts writes.
- Dead code: `latex_renderer/`, `test-faangpath/` route, legacy deploy files
  (Caddy, compose.prod, deploy.sh, alpine Dockerfile).
- No error tracking, no product analytics: there is no way to see a 500 or a
  drop-off today.
- AI calls are synchronous (5–30 s) on 2 workers × 4 threads.
- `views.py` (2.9k lines) and `dashboard_agentic.html` (2.7k lines) are the
  maintenance hot spots.

## What to do, in order

1. Remove what nobody uses (API viewset, dead code, legacy builder,
   overlapping tools, legacy deploy files, stale docs).
2. Make it sellable: public pricing with a plan table, ToS + refund policy,
   one AI-credit pool instead of three AI counters, one import entry point,
   fix the chat-history leak, let a new user import before verifying.
3. Payments through a merchant of record that accepts a Turkish individual
   (see `.claude/integrations/payments.md`).
4. Sentry for errors that break a user's flow; clean 4xx/5xx answers.
5. PostHog funnel events from landing to purchase.
6. Then talk features (see `feature-tree.md`, "Later").
