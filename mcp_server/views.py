"""
The MCP endpoint: one URL, POST only, JSON-RPC in the body.

The shape of this view follows the Streamable HTTP binding. Three things about
it are worth stating up front, because each is a deliberate reading of the
spec rather than the only option:

  * **We always answer with a single JSON object**, never an SSE stream. The
    spec lets the server choose per request. Our tools are database reads and
    writes that finish in milliseconds; a stream would buy nothing and would
    cost us a worker held open behind gunicorn.

  * **We never mint a session id.** The modern revision removed sessions
    outright, and the legacy revision only ever made them optional. Staying
    stateless means any of our two workers can answer any request.

  * **Bearer tokens only, no cookies.** Accepting the site session here would
    make /mcp a CSRF target: a page on another origin could POST a tool call
    and the browser would attach the cookie. A token has to be pasted in
    deliberately, so there is nothing for a third-party page to ride on.
"""

import json
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.authtoken.models import Token

from . import protocol, registry
from . import tools as _tools  # noqa: F401  (importing registers the tools)
from .protocol import ProtocolError

logger = logging.getLogger(__name__)

# How long a client may cache what we tell it. The tool list is fixed at
# deploy time, so an hour is honest; discovery is cheap either way.
TOOLS_TTL_MS = 3_600_000
DISCOVER_TTL_MS = 3_600_000

RATE_LIMIT_CALLS = 120
RATE_LIMIT_WINDOW = 60


def _json(payload, status=200):
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    return response


def _allowed_origin(origin):
    """
    Origin is the browser's word for "who asked". A real MCP client does not
    send one; a web page does. So an Origin we do not recognise means a page is
    trying to use a token it should not have, and the spec says answer 403.
    """
    if not origin:
        return True
    host = urlparse(origin).hostname
    if not host:
        return False
    if host in ("localhost", "127.0.0.1"):
        return True
    allowed = set()
    for entry in settings.CSRF_TRUSTED_ORIGINS:
        parsed = urlparse(entry).hostname
        if parsed:
            allowed.add(parsed)
    return host in allowed


def _authenticate(request):
    """
    Read the bearer token. `Bearer` is what MCP clients send; `Token` is what
    the rest of our API uses, and accepting both spares the user from caring
    which door they came through.
    """
    header = request.headers.get("Authorization", "")
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() not in ("bearer", "token"):
        return None
    try:
        return Token.objects.select_related("user").get(key=parts[1]).user
    except Token.DoesNotExist:
        return None


def _rate_limited(user):
    key = f"mcp_rate_{user.pk}"
    # add() only sets the counter when it is not already there, so the window
    # starts on the first call and expires on its own.
    cache.add(key, 0, RATE_LIMIT_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:  # the key expired between add() and incr()
        return False
    return count > RATE_LIMIT_CALLS


def _is_modern(body, header_version):
    """
    Which era is this client from?

    A modern request declares its version inside `params._meta`; a legacy one
    opens with `initialize`. We also count a modern version in the
    `MCP-Protocol-Version` header, so a client that announces itself as modern
    but forgets the `_meta` block gets told exactly that by header validation
    instead of being quietly served under the old rules.
    """
    params = body.get("params")
    if isinstance(params, dict):
        meta = params.get("_meta")
        if isinstance(meta, dict) and protocol.META_VERSION in meta:
            return True
    if body.get("method") == "initialize":
        return False
    return header_version in protocol.MODERN_VERSIONS


def _check_modern_headers(request, body):
    """
    The HTTP binding mirrors parts of the body into headers so proxies can
    route without parsing JSON. That only works if the two agree, so the spec
    makes the server the referee: any disagreement is a 400 with -32020.
    """
    method = body.get("method")
    params = body.get("params") or {}
    meta = params.get("_meta") or {}
    body_version = meta.get(protocol.META_VERSION)

    header_version = request.headers.get("MCP-Protocol-Version")
    if not header_version:
        raise ProtocolError(
            protocol.HEADER_MISMATCH,
            "Missing required MCP-Protocol-Version header.",
        )
    if header_version != body_version:
        raise ProtocolError(
            protocol.HEADER_MISMATCH,
            f"Header mismatch: MCP-Protocol-Version header value "
            f"'{header_version}' does not match body value '{body_version}'",
        )
    if body_version not in protocol.SUPPORTED_VERSIONS:
        raise protocol.unsupported_version(body_version)

    header_method = request.headers.get("Mcp-Method")
    if not header_method:
        raise ProtocolError(
            protocol.HEADER_MISMATCH, "Missing required Mcp-Method header."
        )
    if header_method != method:
        raise ProtocolError(
            protocol.HEADER_MISMATCH,
            f"Header mismatch: Mcp-Method header value '{header_method}' "
            f"does not match body value '{method}'",
        )

    # Mcp-Name carries whatever the request is about, so a proxy can rate-limit
    # one tool without reading the body. Required only where there is a name.
    if method in ("tools/call", "resources/read", "prompts/get"):
        expected = params.get("name") if method != "resources/read" else params.get("uri")
        header_name = request.headers.get("Mcp-Name")
        if header_name is None:
            raise ProtocolError(
                protocol.HEADER_MISMATCH,
                f"Missing required Mcp-Name header for {method}.",
            )
        if protocol.decode_header_value(header_name) != expected:
            raise ProtocolError(
                protocol.HEADER_MISMATCH,
                f"Header mismatch: Mcp-Name header value does not match body "
                f"value '{expected}'",
            )

    return body_version


# --------------------------------------------------------------------------
# Methods
# --------------------------------------------------------------------------


def _initialize(params):
    """The legacy handshake. Modern clients never send this."""
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": protocol.negotiate_legacy(requested),
        "capabilities": protocol.capabilities(),
        "serverInfo": protocol.SERVER_INFO,
        "instructions": protocol.INSTRUCTIONS,
    }


def _discover():
    """
    The modern replacement for the handshake. It is a plain request with no
    state behind it, so a client may call it, skip it, or cache it.
    """
    return {
        "resultType": "complete",
        "supportedVersions": protocol.SUPPORTED_VERSIONS,
        "capabilities": protocol.capabilities(),
        "instructions": protocol.INSTRUCTIONS,
        "ttlMs": DISCOVER_TTL_MS,
        "cacheScope": "public",
        "_meta": {protocol.META_SERVER_INFO: protocol.SERVER_INFO},
    }


def _tools_list(modern):
    result = {"tools": registry.descriptors()}
    if modern:
        # The tool list is identical for every caller, so it is safe for a
        # client to share one copy across users — that is what "public" means.
        result["resultType"] = "complete"
        result["ttlMs"] = TOOLS_TTL_MS
        result["cacheScope"] = "public"
    return result


def _tools_call(params, user, request, modern):
    name = params.get("name")
    if not name:
        raise ProtocolError(protocol.INVALID_PARAMS, "Missing tool name.")
    if not registry.exists(name):
        # An unknown tool is a malformed request, not something the model can
        # talk its way out of, so it is a protocol error rather than isError.
        raise ProtocolError(
            protocol.INVALID_PARAMS, f"Unknown tool: {name}"
        )
    result = registry.call(name, params.get("arguments") or {}, user=user, request=request)
    if modern:
        result["resultType"] = "complete"
    return result


def _dispatch(method, params, user, request, modern):
    if method == "server/discover":
        return _discover()
    if method == "initialize":
        return _initialize(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return _tools_list(modern)
    if method == "tools/call":
        return _tools_call(params, user, request, modern)
    raise ProtocolError(
        protocol.METHOD_NOT_FOUND, f"Method not found: {method}", http_status=404
    )


# --------------------------------------------------------------------------
# The view
# --------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST", "GET", "DELETE", "OPTIONS"])
def mcp_endpoint(request):
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Allow"] = "POST"
        return response

    if request.method in ("GET", "DELETE"):
        # Older clients used GET for a standing SSE stream and DELETE to end a
        # session. Neither exists any more; saying so plainly is what the spec
        # asks for and it tells a confused client to stop trying.
        response = HttpResponse(
            "The MCP endpoint accepts POST only. The GET stream and session "
            "termination were removed in protocol version 2026-07-28.",
            status=405,
            content_type="text/plain",
        )
        response["Allow"] = "POST"
        return response

    if not _allowed_origin(request.headers.get("Origin")):
        # No id: we never got far enough to read one.
        return _json(
            protocol.failure(None, protocol.INVALID_REQUEST, "Origin not allowed."),
            status=403,
        )

    user = _authenticate(request)
    if user is None or not user.is_active:
        response = _json(
            protocol.failure(
                None,
                protocol.INVALID_REQUEST,
                "Authentication required. Send `Authorization: Bearer <token>` "
                "with the API token from your ResuStack profile page.",
            ),
            status=401,
        )
        response["WWW-Authenticate"] = 'Bearer realm="resustack"'
        return response

    if _rate_limited(user):
        return _json(
            protocol.failure(None, protocol.INTERNAL_ERROR, "Too many requests."),
            status=429,
        )

    try:
        body = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _json(protocol.failure(None, protocol.PARSE_ERROR, "Parse error."), status=400)

    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        # Batches were dropped from the protocol; a list lands here too.
        return _json(
            protocol.failure(None, protocol.INVALID_REQUEST, "Expected a single JSON-RPC 2.0 message."),
            status=400,
        )

    method = body.get("method")
    if not isinstance(method, str):
        return _json(
            protocol.failure(body.get("id"), protocol.INVALID_REQUEST, "Missing method."),
            status=400,
        )

    request_id = body.get("id")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return _json(
            protocol.failure(request_id, protocol.INVALID_PARAMS, "`params` must be an object."),
            status=400,
        )

    modern = _is_modern(body, request.headers.get("MCP-Protocol-Version"))
    try:
        if modern:
            _check_modern_headers(request, body)
    except ProtocolError as exc:
        return _json(
            protocol.failure(request_id, exc.code, exc.message, exc.data),
            status=exc.http_status,
        )

    # A notification has no id and expects no answer — only an acknowledgement.
    if request_id is None:
        if method.startswith("notifications/"):
            return HttpResponse(status=202)
        return _json(
            protocol.failure(None, protocol.INVALID_REQUEST, "Requests must carry an id."),
            status=400,
        )

    try:
        result = _dispatch(method, params, user, request, modern)
    except ProtocolError as exc:
        return _json(
            protocol.failure(request_id, exc.code, exc.message, exc.data),
            status=exc.http_status,
        )
    except Exception:
        logger.exception("MCP request failed: %s", method)
        return _json(
            protocol.failure(request_id, protocol.INTERNAL_ERROR, "Internal error."),
            status=500,
        )

    return _json(protocol.success(request_id, result))
