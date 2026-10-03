# OpenAI — text generation

**Status:** live · **Code:** `resume/openai_engine.py`, `resume/services/agent_loop.py`
**Env:** `OPENAI_API_KEY`

## What it does

Everything that *writes* text: PDF/LinkedIn import extraction, bullet
enhancement, the agent's chat turns (native tool calling, streaming), posting
title/company/requirement labels, improvement drafts, translations.
Judgments and scores do not come from OpenAI — they come from TypeSafe
(see `typesafe.md`).

## Rules

- All OpenAI calls go through `resume/openai_engine.py` (agent loop has its
  own client for streaming tool calls — keep both in those two modules).
- Model: `gpt-4o-mini`. `temperature=0` + JSON mode for extraction,
  `0.7` for prose. Always pass `max_tokens`. Timeout 90 s.
- `send_openai_message()` **returns an error string instead of raising**.
  Callers check for the `"OpenAI API"` / `"Error:"` prefix and answer 503.
  When a call fails in a way the user sees, report it to Sentry with
  `core.observability.report_degraded(...)` (no content in the event).
- Never log prompts or responses — ids, lengths, token counts and error types
  only. The privacy policy promises this.
- Tests never reach the API: mock `send_openai_message` / the client.

## Cost notes

Import ≈ 2k tokens, chat turn ≈ 1–3k tokens, enhancement < 1k. All cheap;
quotas exist to stop abuse, not to recover cost.

## Privacy

OpenAI is a named processor in `privacy.html` and `privacy_tr.html` and in the
sign-up consent text. A new kind of data sent to OpenAI means updating both
policies in the same change.
