# MCP server + MCP Registry

**Status:** live (registry listing `com.resustackapp/resustack`) · **Code:** `mcp_server/`, `server.json`
**Env:** `MCP_REGISTRY_AUTH` (public-key proof record, starts with `v=MCPv1;`)

## What it is

One streamable-HTTP endpoint at `/mcp` (POST; `/mcp/` answers too). The
client model structures the text; ResuStack stores it, versions it and renders
the PDF. Tools: `list_resumes`, `get_resume`, `create_resume`,
`update_resume`, `set_template`, `list_templates`, `set_focus_areas`,
`render_pdf`, `check_quota`, `evaluate_posting`, `list_evaluations`.
Prompt: `focus_areas_from_my_work`.

## Design rules

- **No delete tool.** Removing a resume stays on the website.
- Every write takes a restore point first (`ResumeRevision`).
- `render_pdf` returns a signed link that works once and expires in minutes.
- `evaluate_posting` returns the requirement table, no prose — the calling
  model writes the advice. `target` (branch|base) is required so the client
  asks the person. There is no improve tool over MCP.
- Quotas are the same as on the website.
- `mcp_server.tools.CONTENT_SCHEMA` is an alias of
  `resume_content.CONTENT_SCHEMA`.

## Auth

Bearer token from Profile → API token (`rest_framework.authtoken.Token`).
`Authorization: Bearer <token>` (or `Token <token>`).
claude.ai custom connectors expect OAuth 2.1 — not implemented; this is the
one change that would let non-developers connect.

```bash
claude mcp add --transport http resustack https://resustackapp.com/mcp --header "Authorization: Bearer YOUR_TOKEN"
```

## Registry

- `server.json` `version` must equal `SERVER_INFO["version"]`
  (`mcp_server/protocol.py`); `description` ≤ 100 characters.
- Republish only when `server.json` changes (description, URL, auth method):
  bump both versions, push, then the owner runs
  `mcp-publisher login http …` and `mcp-publisher publish`.
- `/.well-known/mcp-registry-auth` serves `MCP_REGISTRY_AUTH` and refuses
  anything not starting with `v=MCPv1;`. The private key (`key.pem`) is never
  committed.
