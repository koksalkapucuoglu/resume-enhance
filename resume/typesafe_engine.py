"""
Typed judgments from TypeSafe's System One model (Jev).

OpenAI writes text; Jev answers questions about text with a probability — "is
this line a requirement?", "does the resume evidence it?", "is this value in
the PDF?". Every Jev call goes through `ask`, the way every OpenAI call goes
through `openai_engine`.

`ask` never raises. A missing key, a timeout or an API error returns None, and
each caller decides what that means: fall back to the old path, skip a check,
or let the user through. Nothing in the product may depend on Jev being up.

Logs carry the purpose, sizes, token usage and error type — never the state or
the answers, which are resume text and judgments about it.
"""

import logging
import time
from dataclasses import dataclass, field

from django.conf import settings

from typesafe_sdk import Choice, Noul, RetryPolicy, Score, TypeSafeClient, TypeSafeError

logger = logging.getLogger(__name__)

__all__ = ["Answers", "Choice", "Noul", "Score", "ask", "is_configured"]

# One request carried 200 questions in under a second when measured; beyond
# this `ask` splits the batch so a very long posting cannot hit an API limit.
MAX_QUESTIONS_PER_REQUEST = 150

_client = None


@dataclass(frozen=True)
class ChoiceResult:
    choice: str
    confidence: float
    probabilities: dict


@dataclass(frozen=True)
class ScoreResult:
    # Probability-weighted position on the levels, 0 .. len(levels) - 1.
    score: float
    confidence: float
    probabilities: dict
    levels: int

    @property
    def fraction(self):
        """The score on a 0..1 scale, whatever the number of levels."""
        return self.score / (self.levels - 1) if self.levels > 1 else 0.0

    @property
    def level(self):
        """The single most likely level."""
        return max(self.probabilities, key=self.probabilities.get)


@dataclass(frozen=True)
class Answers:
    """The answers to one `ask`, keyed by the ids the caller chose."""

    nouls: dict = field(default_factory=dict)
    choices: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    model: str = ""


def is_configured():
    return bool(settings.TYPESAFE_API_KEY)


def _get_client():
    global _client
    if _client is None:
        _client = TypeSafeClient(
            api_key=settings.TYPESAFE_API_KEY,
            model=settings.TYPESAFE_MODEL,
            timeout=settings.TYPESAFE_TIMEOUT,
            retry=RetryPolicy(max_retries=2, timeout=settings.TYPESAFE_TIMEOUT),
        )
    return _client


def _convert(response, into):
    for key, answer in response.answers.items():
        if answer.type == "noul":
            into["nouls"][key] = float(answer.noul)
        elif answer.type == "choice":
            into["choices"][key] = ChoiceResult(
                choice=answer.choice,
                confidence=float(answer.confidence),
                probabilities=dict(answer.probabilities),
            )
        elif answer.type == "score":
            into["scores"][key] = ScoreResult(
                score=float(answer.score),
                confidence=float(answer.confidence),
                probabilities={int(k): float(v) for k, v in answer.probabilities.items()},
                levels=len(answer.legend),
            )


def ask(state, questions, *, purpose):
    """
    Put `questions` to Jev about `state`.

    `questions` maps ids of your choosing to `Noul`, `Choice` or `Score`.
    Questions in one call run in parallel and cannot see each other's answers.
    `purpose` is a short label for the logs ("job_match.classify").

    Returns `Answers`, or None when Jev is not configured or the call failed.
    """
    if not questions:
        return Answers(model=settings.TYPESAFE_MODEL)
    if not is_configured():
        logger.info("TypeSafe not configured; skipped %s", purpose)
        return None

    items = list(questions.items())
    batches = [
        dict(items[i : i + MAX_QUESTIONS_PER_REQUEST])
        for i in range(0, len(items), MAX_QUESTIONS_PER_REQUEST)
    ]
    collected = {"nouls": {}, "choices": {}, "scores": {}}
    started = time.monotonic()
    input_tokens = output_tokens = 0
    model = settings.TYPESAFE_MODEL

    try:
        client = _get_client()
        for batch in batches:
            response = client.system_one(state=state, questions=batch)
            _convert(response, collected)
            model = response.model or model
            if response.usage:
                input_tokens += response.usage.input_tokens or 0
                output_tokens += response.usage.output_tokens or 0
    except TypeSafeError as exc:
        logger.warning("TypeSafe %s failed: %s", purpose, type(exc).__name__)
        return None
    except Exception as exc:
        # A bug in our conversion must not take the feature down with it; the
        # caller's fallback still applies. The type only: an exception message
        # can quote the answer or the state it failed on.
        logger.warning("TypeSafe %s failed unexpectedly: %s", purpose, type(exc).__name__)
        return None

    logger.info(
        "TypeSafe %s: %d questions, %d requests, %.2fs, tokens in=%d out=%d, model=%s",
        purpose,
        len(items),
        len(batches),
        time.monotonic() - started,
        input_tokens,
        output_tokens,
        model,
    )
    return Answers(model=model, **collected)
