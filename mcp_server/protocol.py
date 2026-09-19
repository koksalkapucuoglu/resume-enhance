"""
The MCP wire protocol: versions, JSON-RPC shapes, and the header rules that
the Streamable HTTP binding adds on top.

Two eras of the protocol are in the wild and they disagree about something
fundamental, so this module names the difference once:

  * **Legacy** (`2025-11-25` and earlier) opens with an `initialize` handshake
    and keeps a session. Every client shipping today speaks this.
  * **Modern** (`2026-07-28`) has no handshake at all. Each request carries its
    own protocol version, client identity, and capabilities in `_meta`, and the
    HTTP binding mirrors some of those into headers so a proxy can route
    without parsing the body.

We answer both. The spec calls that a dual-era server and says the server picks
its behaviour from how the client opens: `_meta` present means modern,
`initialize` means legacy.
"""

import base64
import binascii

# Newest first — this order is what we advertise and what we fall back through.
MODERN_VERSIONS = ["2026-07-28"]
LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS

LATEST_MODERN = MODERN_VERSIONS[0]
LATEST_LEGACY = LEGACY_VERSIONS[0]

# The `_meta` keys a modern request carries. Namespaced by the spec.
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

SERVER_INFO = {"name": "resustack", "version": "1.1.0"}

INSTRUCTIONS = (
    "ResuStack builds and renders resumes. You structure the text; ResuStack "
    "stores it, versions it, and renders the PDF. Every tool that changes "
    "something returns a preview_url the user can open. There is no delete "
    "tool: removing a resume is done by the user on the website. To fill the "
    "\"What I'm working on\" section from what the user has actually been "
    "doing, use the focus_areas_from_my_work prompt, or follow the same steps: "
    "draft, get approval, then set_focus_areas."
)

# JSON-RPC standard codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP's own codes, from the range the spec reserves.
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

# The sentinel a client wraps a header value in when it is not plain ASCII.
B64_PREFIX = "=?base64?"
B64_SUFFIX = "?="


class ProtocolError(Exception):
    """
    A failure that has both a JSON-RPC code and an HTTP status.

    The two travel together because the Streamable HTTP binding is specific
    about the pairing: an unsupported version is 400, an unknown method is 404,
    a header that disagrees with the body is 400.
    """

    def __init__(self, code, message, http_status=400, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data


def decode_header_value(value):
    """
    Undo the `=?base64?...?=` wrapper a client uses for a value that cannot be
    written literally in a header — a Turkish name in `Mcp-Name`, say.
    """
    if not (value.startswith(B64_PREFIX) and value.endswith(B64_SUFFIX)):
        return value
    payload = value[len(B64_PREFIX) : -len(B64_SUFFIX)]
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ProtocolError(
            HEADER_MISMATCH, "Header value is not valid base64.", http_status=400
        )


def success(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def unsupported_version(requested):
    return ProtocolError(
        UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        http_status=400,
        data={"supported": SUPPORTED_VERSIONS, "requested": requested},
    )


def negotiate_legacy(requested):
    """
    Pick the version to answer a legacy `initialize` with.

    A legacy client has no way to retry, so we never reject: if we do not know
    the version it asked for, we answer with the newest legacy one we speak and
    let the client decide whether it can live with that. That is what the
    handshake is for.
    """
    if requested in SUPPORTED_VERSIONS:
        return requested
    return LATEST_LEGACY


def capabilities():
    # No listChanged: the tool set is fixed at deploy time, so there is nothing
    # to notify about, and claiming otherwise would invite a listen stream we
    # would never write to.
    return {"tools": {}, "prompts": {}}
