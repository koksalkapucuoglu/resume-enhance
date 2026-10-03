# TypeSafe (Jev) — typed judgments

**Status:** live · **Code:** `resume/typesafe_engine.py` (+ the services below)
**Env:** `TYPESAFE_API_KEY`, `TYPESAFE_MODEL` (pinned, e.g. `jev-1.13.0`), `TYPESAFE_TIMEOUT`

## Division of labour

OpenAI writes text; Jev answers typed questions about text with probabilities:
`Noul` (probability of yes), `Choice` (one of a set), `Score` (position on
ordered levels). A decision the product acts on, or a number shown to the
user, comes from Jev.

## Where it is used

| Feature | Module | Fails open? |
|---|---|---|
| Import check — is each value supported by the PDF? | `services/import_check.py` | yes — import is stored unchecked |
| Agent tool-call guardrail (block / warn / allow) | `services/agent_guard.py` | yes — allow |
| Posting detection in chat | `views.detect_job_posting` | yes — no offer bar |
| Posting evaluation (score + requirement rows) | `services/job_match.py`, `services/evaluation_service.py` | **no** — `EvaluationError`, a guessed score would break "same yardstick every time" |
| Improvement drafts: unsupported-claim check | `services/improvement_service.py` | yes — lines are not flagged |

## Rules

- All calls go through `typesafe_engine.ask(state, questions, purpose=...)`.
  It never raises; it returns `Answers` or `None`. Every caller has a path for
  `None`.
- The model is pinned so stored scores do not drift. Bump `TYPESAFE_MODEL`
  only after `python manage.py jev_eval` before/after.
- `jev_eval <suite>` runs labelled synthetic cases against the real API
  (paid; never part of `manage.py test`). Suites: `import`, `guard`, `match`.
  A feature that adds Jev judgments adds a suite and picks its threshold there.
- Tests: `TYPESAFE_API_KEY` is blanked under `manage.py test`; mock
  `typesafe_engine.ask`.
- Logging: the SDK logs bodies at DEBUG; the `typesafe_sdk` logger is pinned to
  WARNING. `ask` logs purpose, counts, latency and tokens only.
- One request handles ~200 questions in < 1 s; `ask` splits batches above
  `MAX_QUESTIONS_PER_REQUEST`.

## Privacy

TypeSafe is a named processor in both privacy policies and the sign-up consent.
