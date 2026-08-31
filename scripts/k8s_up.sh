#!/usr/bin/env bash
# Bring the platform up on a local kind cluster.
#
# Idempotent: re-running rebuilds the image, reloads it and rolls the
# Deployment.
#
# Two secrets are generated on each run and piped straight to kubectl. Neither
# touches the repository, and neither appears in a command line -- the
# credential table reaches the cluster through a file descriptor, not an
# argument, because argv is visible to every process on the host.
#
# Since V2.6 the API refuses to start without credentials. That is deliberate,
# and this script does not work around it: it creates the credential Secret the
# Deployment mounts. What lands in the cluster is a table of sha256 digests;
# the tokens themselves are written only to $TOKENS_OUT, at mode 600, outside
# the repository, for whatever needs to talk to the API afterwards.
set -euo pipefail

CLUSTER="${CLUSTER:-agent-platform}"
IMAGE="${IMAGE:-agent-platform:v2.6}"
NS=agent-platform
TOKENS_OUT="${TOKENS_OUT:-$(mktemp -t agent-platform-tokens.XXXXXX)}"

python="${PYTHON:-python}"

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 180s

docker build -t "$IMAGE" .
kind load docker-image "$IMAGE" --name "$CLUSTER"

kubectl apply -f k8s/namespace.yaml

# The grant signing secret. Unchanged in spirit from earlier versions.
kubectl -n "$NS" get secret agent-platform-secrets >/dev/null 2>&1 || \
"$python" -c "import secrets;print('''apiVersion: v1
kind: Secret
metadata: {name: agent-platform-secrets, namespace: agent-platform}
type: Opaque
stringData:
  EXECUTION_GRANT_SECRET: ''' + secrets.token_urlsafe(32) + '''
  POSTGRES_PASSWORD: ''' + secrets.token_urlsafe(24))" | kubectl apply -f -

# The API credentials. Minted here, digests to the cluster, tokens to a file
# only the invoking user can read.
#
# Three identities with disjoint authority, because the separation between
# asking for a high-risk action and approving one is the property the platform
# exists to make executable -- a single all-powerful credential would make it
# decorative in exactly the deployment used to demonstrate it.
"$python" - "$TOKENS_OUT" <<'PY' | kubectl apply -f -
import json
import os
import pathlib
import sys

sys.path.insert(0, "src")
from agent_platform.security.api_auth import issue_token

wanted = {
    "runner": "runs:write",
    "approver": "confirm:write",
    "scraper": "metrics:read",
}

lines, tokens = [], {}
for principal, scopes in wanted.items():
    key_id, token, digest = issue_token()
    lines.append(f"    {key_id} {principal} {scopes} {digest}")
    tokens[principal] = token

out = pathlib.Path(sys.argv[1])
out.write_text(json.dumps(tokens), encoding="utf-8")
os.chmod(out, 0o600)

body = "\n".join(lines)
print(
    "apiVersion: v1\n"
    "kind: Secret\n"
    "metadata: {name: agent-platform-auth, namespace: agent-platform}\n"
    "type: Opaque\n"
    "stringData:\n"
    "  keys: |\n"
    f"{body}"
)
# Only the key ids -- public by construction -- reach stderr. No token, ever.
print(
    "minted credentials: "
    + ", ".join(sorted(wanted)),
    file=sys.stderr,
)
PY

kubectl apply -k k8s/

# Force the API to pick up the credentials minted above.
#
# Without this the script is idempotent in the wrong way. `kubectl apply` does
# not roll a Deployment whose spec has not changed, and replacing a mounted
# Secret does not restart anything -- the kubelet refreshes the file in place,
# but the platform reads the credential table once at start-up. So a re-run
# would install new credentials, hand you the matching tokens, and leave pods
# authenticating against the previous table: every request 401, with the
# cluster and the token file both looking correct.
#
# Found by re-running this script and watching 14 of the 23 HTTP proofs fail
# while the Secret and the token file agreed with each other.
kubectl -n "$NS" rollout restart deployment/agent-platform-api

kubectl -n "$NS" rollout status deployment/agent-platform-redis --timeout=120s
kubectl -n "$NS" rollout status statefulset/agent-platform-postgres --timeout=300s
kubectl -n "$NS" rollout status deployment/agent-platform-api --timeout=300s

echo
echo "Ready. API credentials (tokens) were written to:"
echo "  $TOKENS_OUT"
echo
echo "Forward the Service with:"
echo "  kubectl -n $NS port-forward svc/agent-platform-api 8000:8000"
echo
echo "The API is authenticated. Requests need a bearer token from that file;"
echo "an unauthenticated caller gets 401, which is the design working."
