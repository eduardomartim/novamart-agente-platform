#!/usr/bin/env bash
# Bring the platform up on a local kind cluster.
#
# Idempotent: re-running rebuilds the image, reloads it and rolls the
# Deployment. The secret is generated on each run and piped straight to
# kubectl -- it never touches disk and never appears in a command line.
set -euo pipefail

CLUSTER="${CLUSTER:-agent-platform}"
IMAGE="${IMAGE:-agent-platform:v2.4}"
NS=agent-platform

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 180s

docker build -t "$IMAGE" .
kind load docker-image "$IMAGE" --name "$CLUSTER"

kubectl apply -f k8s/namespace.yaml

kubectl -n "$NS" get secret agent-platform-secrets >/dev/null 2>&1 || \
python -c "import secrets;print('''apiVersion: v1
kind: Secret
metadata: {name: agent-platform-secrets, namespace: agent-platform}
type: Opaque
stringData:
  EXECUTION_GRANT_SECRET: ''' + secrets.token_urlsafe(32))" | kubectl apply -f -

kubectl apply -k k8s/
kubectl -n "$NS" rollout status deployment/agent-platform-redis --timeout=120s
kubectl -n "$NS" rollout status deployment/agent-platform-api --timeout=300s

echo
echo "Ready. Forward the Service with:"
echo "  kubectl -n $NS port-forward svc/agent-platform-api 8000:8000"
