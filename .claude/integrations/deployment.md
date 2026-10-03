# Deployment — Dokploy + Cloudflare

**Status:** live at `https://resustackapp.com`
**Code:** `Dockerfile`, `entrypoint.sh`

## Flow

```
git push origin main
  → Dokploy (GitHub webhook) builds the Dockerfile
  → container starts entrypoint.sh: migrate → collectstatic → gunicorn
  → Traefik (Dokploy) → gunicorn :8000 (2 workers × 4 threads, 120 s timeout)
  → PostgreSQL 15 (Dokploy-managed service)
Cloudflare in front: proxy ON, SSL mode "Full", DDoS/CDN.
```

**Every push to `main` deploys.** Run the whole suite first:

```bash
docker compose exec web python manage.py test resume core mcp_server --parallel 4
```

## Environment

Set in the Dokploy UI (never committed). Per-integration variables are listed
in each `integrations/*.md`. Core:

| Variable | Notes |
|---|---|
| `SECRET_KEY` | required in prod |
| `DEBUG` | `False` in prod (enables HTTPS redirect, secure cookies) |
| `ALLOWED_HOSTS` | `resustackapp.com,www.resustackapp.com` |
| `POSTGRES_*` / `DATABASE_URL` | Dokploy DB service |
| `CACHE_LOCATION` | shared cache (agent approvals, rate limits) |

## Local

```bash
cp .env.example .env   # fill OPENAI_API_KEY at least
docker compose up -d --build
docker compose exec web python manage.py migrate
```

The web container runs the same entrypoint (gunicorn), so code changes need
`docker compose restart web`.

## Notes

- Cloudflare proxy must stay on: Traefik's Let's Encrypt is disabled
  ("Encrypt: None").
- The old Caddy/compose.prod/SSH deploy path was removed in October 2026.
- Print-affecting changes: verify with a real multi-page WeasyPrint render
  (PDF → PNG with macOS `sips`), not only the browser preview.
