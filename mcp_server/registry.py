"""
The tool registry behind `tools/list` and `tools/call`.

Tools live here rather than reusing `resume.services.agent_tools` on purpose.
That registry answers our own chat UI: its results carry `ui` instructions for
a browser to draw, and it reads context like "the active resume" that only the
side panel has. MCP has neither. Keeping the two apart also means the MCP tool
surface is a public contract we can hold still while the internal one moves.
"""

import json
import logging

logger = logging.getLogger(__name__)

_REGISTRY = {}


class ToolError(Exception):
    """
    Something the model can fix by trying again differently — a bad id, a
    quota that ran out, a malformed field.

    These come back as a normal result with `isError: true` rather than as a
    JSON-RPC error, because the spec reserves protocol errors for malformed
    requests and reports recoverable failures in-band so the model sees them.
    """


def tool(name, description, input_schema, title=None, output_schema=None,
         read_only=True, idempotent=False):
    """
    Register a tool.

    `read_only` and `idempotent` become MCP annotations. There is no
    `destructive` flag because nothing here destroys anything: deletion stays
    on the website, where a person is the one clicking.
    """

    def decorator(func):
        annotations = {
            "readOnlyHint": read_only,
            "destructiveHint": False,
            "idempotentHint": idempotent if not read_only else True,
            "openWorldHint": False,
        }
        _REGISTRY[name] = {
            "name": name,
            "title": title or name.replace("_", " ").title(),
            "description": description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "annotations": annotations,
            "handler": func,
        }
        return func

    return decorator


def descriptors():
    """The `tools` array of a `tools/list` result, in a stable order."""
    out = []
    for name in sorted(_REGISTRY):
        entry = _REGISTRY[name]
        descriptor = {
            "name": entry["name"],
            "title": entry["title"],
            "description": entry["description"],
            "inputSchema": entry["input_schema"],
            "annotations": entry["annotations"],
        }
        if entry["output_schema"]:
            descriptor["outputSchema"] = entry["output_schema"]
        out.append(descriptor)
    return out


def exists(name):
    return name in _REGISTRY


def call(name, arguments, user, request=None):
    """
    Run a tool and shape its return into an MCP tool result.

    A handler returns `(text, data)`: the sentence the model reads, and the
    structured payload. Both are sent — `structuredContent` for clients that
    can use it, and the same JSON in a text block for those that cannot.
    """
    entry = _REGISTRY.get(name)
    if entry is None:
        # Unknown tool is a protocol-level mistake, not something to retry.
        raise KeyError(name)

    if not isinstance(arguments, dict):
        raise ToolError("`arguments` must be an object.")

    try:
        text, data = entry["handler"](user=user, request=request, **arguments)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except TypeError as exc:
        # A missing or unexpected argument: the model can correct this.
        logger.info("MCP tool %s called with bad arguments: %s", name, exc)
        return {
            "content": [{"type": "text", "text": f"Invalid arguments for {name}: {exc}"}],
            "isError": True,
        }
    except Exception:
        logger.exception("MCP tool %s failed", name)
        return {
            "content": [{"type": "text", "text": f"{name} failed. Nothing was changed."}],
            "isError": True,
        }

    content = [{"type": "text", "text": text}]
    result = {"content": content, "isError": False}
    if data is not None:
        result["structuredContent"] = data
        content.append({"type": "text", "text": json.dumps(data, ensure_ascii=False)})
    return result
