# Kubernetes

Two API replicas behind a Service, sharing state through Redis, validated by
running them — not by the manifests parsing.

```
        Service agent-platform-api (ClusterIP :8000)
                        │
        ┌───────────────┴───────────────┐
   api replica 1                   api replica 2
   uid 10001, read-only rootfs     uid 10001, read-only rootfs
        └───────────────┬───────────────┘
                  Redis (1 pod — not HA)
```

## Running it locally

```bash
kind create cluster --name agent-platform
docker build -t agent-platform:v2.4 .
kind load docker-image agent-platform:v2.4 --name agent-platform

kubectl apply -f k8s/namespace.yaml

# Generate the signing secret without it touching disk or a command line:
python -c "import secrets;print('''apiVersion: v1
kind: Secret
metadata: {name: agent-platform-secrets, namespace: agent-platform}
type: Opaque
stringData:
  EXECUTION_GRANT_SECRET: ''' + secrets.token_urlsafe(32))" | kubectl apply -f -

kubectl apply -k k8s/
kubectl -n agent-platform rollout status deployment/agent-platform-api
kubectl -n agent-platform port-forward svc/agent-platform-api 8000:8000
```

## TLS, locally

```bash
./scripts/k8s_up.sh            # the platform
./scripts/k8s_ingress_up.sh    # the controller, a certificate, the Ingress
```

`scripts/gen_tls_secret.sh` generates the certificate: `openssl` writes the key
into a `mktemp` directory, `kubectl` reads it from there, and a `trap` deletes
it even on failure. The key never enters the working tree and never appears in
an `argv`; `.gitignore` refuses `*.key`, `*.pem` and `*.crt` by extension as a
second line of defence.

**TLS is not authentication.** The Ingress proves the host and encrypts the
conversation. It does not say who the caller is: over HTTPS, a request with no
bearer credential still gets 401, and one with the wrong scope still gets 403.
What changed is the caveat -- a credential no longer relies on the network
being trusted.

Plain HTTP is redirected rather than served. An API that quietly accepts
cleartext is an API whose clients eventually send a credential over it.

### Reaching it

A cluster created from `k8s/kind-config.yaml` maps host ports 80 and 443 into
the node, so once `agent-platform.local` resolves to `127.0.0.1`:

```bash
curl --cacert <the certificate> https://agent-platform.local/health
```

kind bakes those mappings in at creation, so an **existing** cluster cannot
gain them -- `scripts/k8s_up.sh` deliberately never recreates one, because
recreating destroys everything in it. Forward the controller instead:

```bash
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8443:443
curl -k --resolve agent-platform.local:8443:127.0.0.1 https://agent-platform.local:8443/health
```

`-k` skips the certificate check: fine for a local demo, not fine anywhere a
credential is sent to a real host.

### What this is not

A self-signed certificate for a hostname that does not exist. Production would
use a real CA -- cert-manager with ACME, or one issued by the organisation --
and this is deliberately not that. It demonstrates that the edge terminates TLS
and that the application is unchanged by it.

### Not yet executed

`scripts/tls_proofs.py` holds the live HTTPS proofs: the certificate covers the
hostname, plain HTTP redirects, HTTPS reaches the application, and an
unauthenticated request over TLS is still refused. They are written and have
**not been run** -- the Docker daemon was down for the whole of V2.9, so no
cluster existed to run them against. Twenty-one static checks cover what can be
verified without one.

Tearing it down:

```bash
kind delete cluster --name agent-platform
```

`secret.example.yaml` is a template with a placeholder and is deliberately not
listed in `kustomization.yaml` — applying it would install a publicly known
string as the signing key.

## What each probe is for

The distinction between the two is the whole point, so it is worth stating.

| Probe | Endpoint | Checks dependencies? |
|---|---|---|
| `startupProbe` | `/health` | no — 90s of grace while Redis arrives |
| `livenessProbe` | `/health` | **no, deliberately** |
| `readinessProbe` | `/ready` | **yes** — repository, registry, Redis |

A liveness probe that checked Redis would restart every replica simultaneously
the moment Redis blipped, turning a degradation into an outage. Readiness takes
the pod out of the Service instead, and puts it back when the dependency
returns. **Validated:** with Redis scaled to zero, all API pods went `0/1`
NotReady while `/health` kept returning 200 — none were killed — and all
returned to `1/1` on their own when Redis came back.

## Startup and Redis

If `REDIS_URL` is set and Redis is unreachable, the platform **refuses to
construct** and the container exits. That is fail-closed and intended: starting
without shared state would mean silently enforcing a rate limit N times weaker
than configured.

In practice this shows up as one restart per pod on a cold `kubectl apply`, when
the API starts before Redis is accepting connections. The `startupProbe` gives
it room; the Deployment recovers on its own.

## Observability

**Structured logs.** JSON, one object per line, every value through the same
`sanitize_text` the response boundary uses. Fields: `ts`, `level`, `logger`,
`message`, plus `request_id`/`trace_id` when inside a request.

`uvicorn` is started with `log_config=None` so it does not install its own
handlers over the formatter. Without that, every line — including the access
log — came out as plain text and never passed the sanitiser. That was found by
reading the logs of a running pod, not the code.

**Correlation IDs.** Every response carries `X-Request-ID`. An inbound one is
honoured so a caller can stitch its logs to ours, but it is truncated to 64
characters and stripped to `[A-Za-z0-9_-]` — it reaches log output, and an
unvalidated header that reaches a log is a log-injection vector.

**Metrics.** `/metrics`, Prometheus text format, hand-written rather than via a
client library. `/metrics` is a new egress path, and writing the exposition
means every byte that leaves is one the code chose: no auto-collection, no
registry another library can add to, and no label carrying a value from outside
the platform.

The pods carry `prometheus.io/scrape` annotations. **No Prometheus is deployed
here** — the endpoint is validated by scraping it directly.

**Distributed tracing is not implemented.** OpenTelemetry would add a
dependency, a collector, and an egress path that does not pass the sanitiser.
Correlation IDs solve the operational problem — following one request across
replicas — without that surface. Future work, honestly labelled.

## Security posture

Validated against running pods, not against the YAML:

- `uid=10001`, non-root — confirmed with `id` in the container
- `readOnlyRootFilesystem` — confirmed: `touch /probe` → *Read-only file system*
- `/var/lib/agent-platform` writable, and nothing else
- `capabilities: drop [ALL]`, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`
- `automountServiceAccountToken: false` — the app never calls the Kubernetes API, so no token is mounted
- **No `GEMINI_API_KEY`** in the ConfigMap, the Secret, or any pod environment
- The grant signing secret appears **0 times** across all pod logs

### NetworkPolicy — enforced, and proven

Default-deny for the namespace, then: API ingress on 8000 from inside the
namespace; API egress to Redis and DNS only; Redis ingress from the API only,
and no egress at all.

`kindnet` does enforce these. That was established by connectivity test rather
than assumed from the objects existing:

| State | A non-API pod reaching Redis |
|---|---|
| policies applied | **BLOCKED** |
| policies removed | `PONG` |
| policies restored | **BLOCKED** |

## Autoscaling

HPA on CPU, 2→5 replicas at 60% utilisation, backed by `metrics-server` v0.9.0.

**Validated with a real scale-up.** Under 320 concurrent stub requests:

```
SuccessfulRescale   New size: 4; reason: cpu resource utilization
                    (percentage of request) above target
ScalingReplicaSet   Scaled up replica set from 2 to 4
```

**The honest caveat:** CPU is a weak signal for this workload. A live request
spends most of its time blocked on a model call, not burning CPU, so under real
traffic this would scale later than it should. The correct signal is queue
depth, which needs KEDA or a custom metrics adapter — out of scope. What is
demonstrated is that the mechanism works end to end and acts on real metrics.

## Shared state, and what is not shared

**Shared via Redis** — this is what makes more than one replica correct:
pending confirmations, the LangGraph checkpoint, the provider-call ledger, the
rate-limiter window, and grant nonces.

**Validated:** a high-risk action suspended on replica A was approved on replica
B — a different pod that never saw the original request — and the tool ran.
Replaying the same confirmation against A returned **404**.

**Still per-replica:**

| State | Consequence |
|---|---|
| **SQLite** | Each pod has its own event history and cost ledger in an `emptyDir`. It dies with the pod. |
| **Money budget** | `BudgetGuard` reads daily spend from the repository, so the dollar ceiling is **per replica**: two replicas allow 2× the configured daily spend. In stub mode cost is $0, so this deployment is unaffected — but it is a real gap. The ceiling that actually binds, the physical provider-call ledger, *is* shared. |
| **Circuit breaker** | Each replica learns a provider outage independently. Wasteful, not incorrect. |
| **Embedding cache** | Cold per replica. Affects cost only. |

Fixing the first two means moving the repository to Postgres. That is a separate
change and is not pretended here.

## Implemented and validated / Design for production / Out of scope

**Implemented and validated in kind:** 2 replicas Ready · requests distributed
across both · cross-replica confirmation · replay refused across replicas ·
shared rate limiter and provider ledger · pod deletion with automatic recovery
and no dropped requests · rollout restart · readiness reacting to Redis loss and
recovering · JSON logs with no secrets · `/metrics` scrapeable · resource
requests and limits · securityContext · NetworkPolicy enforcement · HPA
scale-up under load.

**Design for production, not validated here:** Redis with replication and
failover · Postgres for the repository · Ingress with TLS · authentication ·
image pinned by digest from a registry · Prometheus and a dashboard · pod
anti-affinity across nodes (this cluster has one node).

**Out of scope:** distributed tracing · service mesh · multi-tenancy · per-caller
identity or key rotation · HTTP transport for MCP.

## Running the test suite on Linux

Some of the suite cannot execute on a Windows host with **Smart App Control**
enforced. That is not a project problem and not something to work around in the
code, so it is worth writing down rather than rediscovering.

`pyarrow` — pulled in by Streamlit, and used by `st.dataframe` — ships native
libraries that are not Authenticode-signed. With Smart App Control enforced
(`VerifiedAndReputablePolicyState = 1`) the loader refuses them:

```
CodeIntegrity 3033/3077:
  ...attempted to load ...\pyarrowrrow.dll that did not meet the
  Enterprise signing level requirements
```

Reinstalling does not help — PyPI wheels generally are not signed — and the
files carry no Mark-of-the-Web, so unblocking them does nothing either. The
policy is correct and stays on. The consequence is that 25 dashboard tests that
render a dataframe cannot run on that host.

They run on Linux, against the same source and the same pinned versions:

```bash
docker build -f Dockerfile.test -t agent-platform-tests:v2.4 .
docker network create ap-test-net
docker run -d --name ap-test-redis --network ap-test-net redis:8-alpine

docker run --rm --network ap-test-net   -e TEST_REDIS_URL=redis://ap-test-redis:6379/0   agent-platform-tests:v2.4
```

Tests that need a Docker daemon skip themselves in that container — it has no
socket — so the Docker-dependent suites are run on the host, where they pass.
Between the two, everything is exercised somewhere.

`Dockerfile.test` has its own `Dockerfile.test.dockerignore`. The production
`.dockerignore` is a strict allow-list that deliberately excludes tests and
fixtures, and running the suite needs them; giving the test build its own
context keeps the shipping image's context exactly as tight as it was.

## Limitations

- **Redis is one pod, with no persistence and no replication.** It is not high
  availability and is not presented as such. If it dies, in-flight
  confirmations are lost and the API goes NotReady until it returns.
- **Single node.** Every pod is on the same machine, so nothing here says
  anything about node failure or scheduling across zones.
- ~~No TLS~~ — closed in V2.9 **for a local cluster**. An ingress-nginx
  controller terminates TLS at `agent-platform.local` with a self-signed
  certificate; the Service is still `ClusterIP`, so the controller remains the
  only route in. Verified by static checks (21 tests); the live HTTPS proofs in
  `scripts/tls_proofs.py` are written and **have not been executed** — the
  Docker daemon was down for the whole phase.
- ~~No authentication~~ — closed in V2.6. Credentials arrive as a Secret mounted
  read-only at `/etc/agent-platform/auth/keys`, not as environment: env is
  readable through `/proc/<pid>/environ`, lands in crash dumps, and is inherited
  by child processes, and this platform spawns one under `TOOL_TRANSPORT=mcp`.
  Anything that reaches the port could previously approve a suspended
  high-risk action. That is why the Service is ClusterIP with no Ingress.
- **Single-tenant** throughout.
- Validated on a local `kind` cluster. **Not run in production**, and nothing
  here should be read as saying otherwise.
