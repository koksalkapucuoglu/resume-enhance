"""
Prompts: text the server offers for the client's model to run locally.

A tool runs here; a prompt runs there. That difference is the point of this
module. Summarising "what I have been working on" needs the user's own
conversation history, which lives in their client and never reaches
ResuStack — so the server cannot do it, but it can hand the client a
well-built instruction for doing it, ending in a call back to
`set_focus_areas`.

Clients surface prompts differently (Claude Code lists them as slash
commands; some clients do not show them at all), so everything a prompt asks
for must also be reachable by a user who simply types the request.
"""

from .protocol import INVALID_PARAMS, ProtocolError


def _focus_areas_text(arguments):
    resume_id = (arguments.get("resume_id") or "").strip()
    period = (arguments.get("period") or "").strip() or "the recent work you can see"

    target = (
        f"resume {resume_id}"
        if resume_id
        else "the resume the user picks (call list_resumes and ask which one)"
    )

    return f"""Draft the "What I'm working on" section of my ResuStack resume.

1. From our conversations — only what you can actually see, covering {period} —
   collect the technical work I have really done. Do not infer skills from a
   single mention, and do not add anything I did not do.
2. Condense it to 3-5 short lines. Each line is one area: the technology, the
   domain, and the kind of problem. Example of the tone: "Django REST Framework
   backends for finance and education platforms: architecture and query
   performance".
3. Leave out client names, internal system names, and anything that reads as
   confidential. When unsure, generalise ("a large education platform").
4. Write the lines in the language the resume is written in.
5. Show me the lines and wait for my approval. Do not save anything before I
   say yes; apply my edits if I make any.
6. Once I approve, call set_focus_areas for {target} with the approved lines
   and include=true — or include=false if I say I want them stored but not
   printed. set_focus_areas changes only this section, so there is no need to
   read or resend the rest of the resume.
7. Give me the preview_url it returns."""


PROMPTS = {
    "focus_areas_from_my_work": {
        "title": "Focus areas from my work",
        "description": (
            "Summarise what the user has actually been working on, from the "
            "conversation history this client can see, into short lines for "
            "the resume's \"What I'm working on\" section. Asks for approval "
            "before saving with set_focus_areas."
        ),
        "arguments": [
            {
                "name": "resume_id",
                "description": "Resume to update, from list_resumes. Optional.",
                "required": False,
            },
            {
                "name": "period",
                "description": 'Time span to cover, e.g. "the last three months". Optional.',
                "required": False,
            },
        ],
        "render": _focus_areas_text,
    },
}


def descriptors():
    """The `prompts` array of a `prompts/list` result, in a stable order."""
    return [
        {
            "name": name,
            "title": entry["title"],
            "description": entry["description"],
            "arguments": entry["arguments"],
        }
        for name, entry in sorted(PROMPTS.items())
    ]


def get(name, arguments):
    """A `prompts/get` result: the prompt rendered with its arguments."""
    entry = PROMPTS.get(name)
    if entry is None:
        raise ProtocolError(INVALID_PARAMS, f"Unknown prompt: {name}")
    if arguments is not None and not isinstance(arguments, dict):
        raise ProtocolError(INVALID_PARAMS, "`arguments` must be an object.")
    # The spec types every prompt argument as a string.
    arguments = {k: str(v) for k, v in (arguments or {}).items()}
    return {
        "description": entry["description"],
        "messages": [
            {
                "role": "user",
                "content": {"type": "text", "text": entry["render"](arguments)},
            }
        ],
    }
