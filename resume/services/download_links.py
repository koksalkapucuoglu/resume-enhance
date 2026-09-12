"""
Short-lived, single-use links to a rendered PDF.

An MCP client cannot hand the user a file — it returns text. So a download is
offered as a link instead. The link carries its own authorisation, because
whoever follows it is a browser without the caller's token.

That makes two properties matter more than usual:

  * It expires quickly. A link that leaks is only useful for minutes.
  * It works once. Otherwise a single issued link is an unlimited download of a
    quota-limited thing, for as long as it lives.
"""

import logging
import uuid

from django.conf import settings
from django.core import signing
from django.core.cache import cache

logger = logging.getLogger(__name__)

SALT = "resume.download"
DEFAULT_MAX_AGE = 600  # ten minutes is longer than anyone needs to click


class DownloadLinkError(Exception):
    """The link is expired, malformed, or already used."""


def max_age():
    return getattr(settings, "DOWNLOAD_LINK_MAX_AGE", DEFAULT_MAX_AGE)


def sign(resume):
    """
    Mint a token for this resume.

    The owner is baked in, so a token cannot be replayed against someone else's
    resume even if the ids were guessable.
    """
    return signing.dumps(
        {"r": resume.pk, "u": resume.user_id, "n": uuid.uuid4().hex}, salt=SALT
    )


def consume(token):
    """
    Validate a token and spend it.

    Returns (resume_id, user_id). Raises DownloadLinkError for anything that
    should not be served — expired, tampered with, or seen before.
    """
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age())
    except signing.SignatureExpired:
        raise DownloadLinkError("This download link has expired.")
    except signing.BadSignature:
        raise DownloadLinkError("This download link is not valid.")

    nonce = payload.get("n")
    if not nonce:
        raise DownloadLinkError("This download link is not valid.")

    # Marking the nonce only needs to outlive the token itself.
    key = f"download_nonce_{nonce}"
    if not cache.add(key, True, max_age() + 60):
        logger.info("Refused a replayed download link for resume %s", payload.get("r"))
        raise DownloadLinkError("This download link has already been used.")

    return payload["r"], payload["u"]
