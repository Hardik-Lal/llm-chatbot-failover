# LLM Chatbot with Key Failover and Response Caching

React + FastAPI + SQLite + LiteLLM. A multi-turn chatbot that survives provider/key failures
(automatic failover across a key pool) and serves repeated prompts from LiteLLM's cache, with
per-request metadata (source, model, latency, tokens) shown next to every reply.

## 1. Live URLs

| | URL |
|---|---|
| Frontend | `https://<your-frontend>.vercel.app` |
| Backend | `https://<your-backend>.onrender.com` |
| Health | `https://<your-backend>.onrender.com/health` |
| Demo video | `<link>` |

## 2. Run locally: with Docker and without

### With Docker
```bash
cp .env.example .env      # then put your real keys in LLM_API_KEYS
make up                   # backend :8000, frontend :3000
make down
```
`make up` also creates `.env` from `.env.example` if it's missing. SQLite lives in the named
volume `appdata` mounted at `/data`, so history survives `make down` / `make up`.

### Without Docker
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key-one,key-two DATABASE_URL=sqlite:///./app.db \
       ALLOWED_ORIGINS=http://localhost:3000 ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000
python -m pytest -q       # backend tests (no real API calls, no keys needed)

# frontend (new terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev    # http://localhost:3000
```

### Environment variables
| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, ≥ 2 keys → `key_1`, `key_2`, … |
| `LLM_MODEL` | LiteLLM model name (default `gemini/gemini-3.1-flash-lite`) |
| `DATABASE_URL` | e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` hook |
| `CACHE_TYPE` / `CACHE_DIR` | `local` (in-memory, default) or `disk` |
| `KEY_COOLDOWN_SECONDS`, `REQUEST_TIMEOUT_SECONDS`, `DEFAULT_SYSTEM_PROMPT` | Optional tuning |
| `VITE_API_URL` | Frontend build-time backend URL |

## 3. Design decisions: key fallback, caching, SQLite session storage

**Key fallback.** `Gateway.complete()` tries keys in order (`key_1`, `key_2`, …) and calls
`litellm.completion(api_key=...)` with `num_retries=0`, so failover is decided by us at key level
instead of LiteLLM retrying the same dead key. Any failure (429, quota, 5xx, timeout, auth) moves
on to the next key and increments `fallback_attempts`. A key that failed for real goes into a short
cooldown (30 s) so later requests try healthy keys first instead of paying a failing call each
time; if everything is cooling down, all keys are still tried. When every key fails the API returns
`503 {"detail":"all providers unavailable"}`, never a stack trace (a catch‑all handler returns JSON
for any unexpected error).

**Failure simulation.** `X-Simulate-Failure` (only when `ENABLE_FAILURE_SIMULATION=true`) marks the
listed keys as failed for that single request without touching real quota. Non-listed keys make a
real LLM call, so a simulated request returns a real answer. Simulated requests bypass the cache so
the failover demo always shows a live answer from the surviving key.

**Caching.** LiteLLM's built-in cache (`litellm.cache = Cache(type="local")`, or `disk`). The cache
key is built from the model and the full message list (system prompt + history + new message); the
API key is *not* part of it, so a reply produced by `key_1` is reusable after failover. A hit is
detected from LiteLLM's `cache_hit` flag → `LLM_CACHE`, `key_id: null`,
`fallback_attempts: 0`, and the token counts stored with the cached response.
The same first message in a brand-new session produces the same message list → cache hit. Any
different message (or a follow-up with different history) produces a different key → live call.

**SQLite session storage.** Plain `sqlite3` (WAL mode), schema created on startup. `messages`
follows the required schema (+ index on `session_id`); a small `sessions` table stores the optional
per-session system prompt. History is loaded from SQLite on every request (the client sends only the
new message). The user and assistant rows are written together in one transaction **only after the
LLM call succeeded**, so failed requests (422/503) leave no trace in history. The system prompt is
never returned by the history endpoint.

**Tokens & latency.** Tokens come from the provider's `usage` for that request only (never
cumulative). `latency_ms` is server-measured with `perf_counter` and returned as an integer.

## 4. Verification: how to see failover, cache hits and per-message token usage

Replace `$API` with the backend URL.

```bash
curl $API/health

# Live call
curl -s $API/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo-1","message":"What is the capital of France?"}'

# Cache hit: same first message, NEW session -> resolution_source LLM_CACHE, key_id null, faster
curl -s $API/chat -H 'content-type: application/json' \
  -d '{"session_id":"demo-2","message":"What is the capital of France?"}'

# Failover: key_1 fails -> answered by key_2, fallback_attempts 1
curl -s $API/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-3","message":"Name a prime number"}'

# All keys fail -> 503 {"detail":"all providers unavailable"}, nothing stored
curl -si $API/chat -H 'content-type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-4","message":"hello"}'

# Stored history (oldest first, no system prompt) and 404 / 422 cases
curl -s $API/sessions/demo-1/messages
curl -si $API/sessions/does-not-exist/messages
curl -si $API/chat -H 'content-type: application/json' -d '{"session_id":"x","message":"   "}'
```
In the UI, each assistant reply shows source, model, latency and the **per-request** input/output
tokens. Open *Session settings* to set a system prompt or pick a simulated failure.

Tests: `make test` (14 tests: validation, cache hit/miss, multi-turn + isolation, per-request
tokens, failover, 503 not stored, real provider failures, CORS, SQLite persistence). They run
LiteLLM's real cache with `mock_response`, so no API keys are needed.

## 5. Setup Used: your editor and AI tools

- Editor: `<your editor, e.g. VS Code>`
- AI tools: Claude (Anthropic) for scaffolding and test generation; all code reviewed and run locally.

## 6. Bonus features (if any): what you added beyond the requirements and why

- Optional per-session system prompt field in the UI (`system-prompt-input`).
- In-UI "Simulate failure" selector so failover can be demoed without curl.
- Per-key cooldown so a dead key doesn't slow every request.
- Optional disk cache (`CACHE_TYPE=disk`) that survives restarts.
- Non-root containers, health checks, catch-all JSON error handler.

## Deployment notes

- **Backend** (Render / Railway / Fly.io): deploy `backend/` as a Docker service; set `LLM_API_KEYS`,
  `LLM_MODEL`, `DATABASE_URL`, `ALLOWED_ORIGINS=<frontend URL>`, `ENABLE_FAILURE_SIMULATION=true`.
  Attach a persistent disk at `/data` if you want history to survive restarts (free Render tiers have
  an ephemeral disk). The container honours the `PORT` variable.
- **Frontend** (Vercel / Netlify / Cloudflare Pages): root `frontend/`, build `npm run build`, output
  `dist`, env `VITE_API_URL=<backend URL>`.
