# Deploying NovaMart

Two independent entry points, built from one repository:

| Service   | What runs                        | Built from                                   | Platform |
|-----------|----------------------------------|----------------------------------------------|----------|
| Dashboard | Streamlit, platform in-process   | `Dockerfile.dashboard` + `railway.toml`      | Railway  |
| API       | Starlette ASGI app (`create_app`) | `api/index.py` + `api/requirements.txt` + `vercel.json` | Vercel   |

The dashboard is **not** a client of the API: it imports `AgentPlatform` and runs
it in its own process. The two share configuration conventions, not a network
path.

**Status, plainly:** neither service has been deployed from this repository yet.
Everything below is what the manifests encode and what the offline test suite
checks (`tests/integration/test_deployment_manifests.py`); the first real deploy
is the first end-to-end proof.

## Dashboard on Railway

`railway.toml` selects the image itself:

```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile.dashboard"
```

Without `dockerfilePath` Railway would find the root `Dockerfile` — the **API**
image — and the dashboard would never run. The `RAILWAY_DOCKERFILE_PATH` service
variable does the same from outside the repository and is not needed.

Health check: `/_stcore/health` (Streamlit's own readiness endpoint). The
image's `CMD` binds `0.0.0.0` on `$PORT`; do not set `PORT` or a start command.

### Variables

| Variable | Needed | Notes |
|----------|--------|-------|
| `VISITOR_ID_SALT` | yes | Secret. Salts the per-visitor quota key derived from `X-Real-IP`. Unset, a random salt per process resets every visitor's window on redeploy. |
| `DATABASE_URL` | recommended | PostgreSQL. Without it the image writes SQLite to `/var/lib/agent-platform`, which is lost on redeploy. |
| `REDIS_URL` | for more than one replica | Shared rate limits, pending confirmations and graph checkpoints. **Requires Redis 8+ or Redis Stack** (the checkpointer creates RediSearch `FT.*` indexes); Upstash's Redis is not compatible. |
| `GEMINI_API_KEY` + `AGENT_PLATFORM_LIVE` | no | Absent, the dashboard runs the deterministic stub, offline. Both must be supplied at run time to call a real model; neither is baked into the image. |

The dashboard trusts exactly one client-address header, `X-Real-IP`, which
Railway's edge writes. Behind a different proxy that trust boundary must be
revisited in `dashboard/app.py`.

### Image build

`Dockerfile.dashboard` installs `requirements-dashboard.txt`, which includes the
hashed API lock (`requirements-api.txt`) and pins every dashboard-only package
by version. Because those pins carry no hashes, the install uses
`--no-require-hashes`: hashes are verified where present. Regenerating the
dashboard pins with `--generate-hashes` (command in the file's header) would
close that gap.

A failed `pip install` fails the build. (It did not always: an `|| true` later
in the same `&&` chain excused it. See the comment above the `RUN` line.)

## API on Vercel

Vercel's Python builder serves the module-level `app` in `api/index.py`, which is
`create_app()` — the same factory the Docker image and the tests use.
`api/requirements.txt` points at `requirements-api.txt`; it sits beside the
entrypoint so Vercel does not stop at the root `pyproject.toml`, whose core
dependencies exclude Starlette.

### What is uploaded

`.vercelignore` denies everything, then allows back `api/`, `src/`,
`requirements-api.txt`, `vercel.json` and `data/kb_vectors.db`. This matters
for CLI deploys: the Vercel CLI's built-in ignore list does not include `.env`,
so without this file `vercel deploy` from a working copy would upload the local
`.env`. Check the upload set without deploying:

```bash
vercel deploy --dry
```

### Variables

| Variable | Needed | Notes |
|----------|--------|-------|
| `API_AUTH_KEYS` (or `API_AUTH_KEYS_FILE`) | yes | Minted with `agent-platform auth new-key`. With no credentials the app refuses to start rather than serve unauthenticated. |
| `REDIS_URL`, `DATABASE_URL` | yes, in practice | Serverless instances do not share memory; without them every instance has its own rate limits and pending confirmations, and SQLite is ephemeral. Same Redis 8+/Stack requirement. |
| `EXECUTION_GRANT_SECRET` | only with `TOOL_TRANSPORT=mcp` | Not recommended on Vercel: the MCP transport spawns a subprocess per call. |

`vercel.json` sets `maxDuration: 300` and `memory: 1024`. The maximum duration a
function may have depends on the Vercel plan; confirm 300 s is allowed on the
account before relying on it.

Request bodies are read with a hard byte ceiling (`MAX_BODY_BYTES`, 397,312
bytes, derived so it never rejects a body the schema accepts); larger bodies
get `413` before they are buffered.

## Before the first deploy

- Run the offline suite and `python scripts/check_protected.py`.
- `docker build -f Dockerfile.dashboard .` locally — CI does not build this image yet.
- Set the secrets in each platform's secret store, never in a committed file.
