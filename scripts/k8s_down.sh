#!/usr/bin/env bash
# Tear it down. `--keep-cluster` removes the workloads but leaves the cluster,
# which is faster when iterating.
set -euo pipefail

CLUSTER="${CLUSTER:-agent-platform}"

if [[ "${1:-}" == "--keep-cluster" ]]; then
  kubectl delete namespace agent-platform --ignore-not-found
else
  kind delete cluster --name "$CLUSTER"
fi
