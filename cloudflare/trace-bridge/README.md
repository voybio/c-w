# Cloudflare Trace Bridge

Public edge endpoint for browser-origin agent traces. It forwards valid payloads to GitHub `repository_dispatch` and never exposes GitHub credentials to clients.

## 1) Prereqs
- Cloudflare account
- Node.js 20+
- `npm i -g wrangler` (or use `npx wrangler`)
- GitHub token with permission to dispatch workflow events for `voybio/c-w`

## 2) Login
```bash
cd cloudflare/trace-bridge
npx wrangler login
```

## 3) Set the GitHub token as Worker secret
```bash
npx wrangler secret put GITHUB_TOKEN
```
Paste your token when prompted.

Notes:
- Paste the raw token only (no quotes, no `Bearer ` prefix).
- Bridge accepts either `GITHUB_TOKEN` or `LOOM_GITHUB_TOKEN` secret names.

## 4) Deploy
```bash
npx wrangler deploy
```
Wrangler returns a URL like:
`https://loom-trace-bridge.<your-subdomain>.workers.dev`

Your trace endpoint is:
`https://loom-trace-bridge.<your-subdomain>.workers.dev/api/trace`

## 5) Smoke test the bridge directly
```bash
curl -i https://loom-trace-bridge.<your-subdomain>.workers.dev/api/trace \
  -H 'Content-Type: application/json' \
  --data '{"agent_id":"smoke-agent","message":"Shamsi is on holiday","trace_id":"smoke-001","source":"manual-smoke"}'
```
Expected: HTTP `202` with `{"status":"accepted", ... }`

## 6) Point the site to this endpoint
Update:
- `design_instructions.md` (`trace_endpoint`)
- `agent-manifest.json` (`trace_vector.endpoint`)
- `server/config.py` (`MANIFEST.trace_vector.endpoint`)

Then push `main` and let your existing Pages workflow publish.

## 7) Hardening checklist (recommended)
- Add a Cloudflare rate limiting rule for `/api/trace` (per-IP cap).
- Keep `ALLOWED_ORIGINS` restricted to your origin.
- Rotate `GITHUB_TOKEN` periodically.
- Keep token scope minimal.
