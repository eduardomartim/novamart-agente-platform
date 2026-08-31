#!/usr/bin/env bash
# Put TLS in front of the platform: an ingress controller, a local certificate,
# and the Ingress that ties them together.
#
# Separate from k8s_up.sh on purpose. That script brings the platform up and is
# run constantly; this one installs a cluster-wide component and is run once.
# Folding them together would mean every deploy reinstalled a controller.
#
# Idempotent. Re-running reinstalls the controller manifest (a no-op when
# unchanged), regenerates the certificate, and restarts the controller so it
# picks the new one up -- nginx caches the certificate it was handed and does
# not notice a replaced Secret on its own.
#
# TLS here is transport security. It is NOT authentication: a request arriving
# over HTTPS with no bearer credential still gets 401. The two controls are
# independent and both apply.
set -euo pipefail

NS="${NS:-agent-platform}"
CONTROLLER_NS=ingress-nginx
HOST="${TLS_HOST:-agent-platform.local}"

echo "==> installing the ingress controller"
# The kind-specific manifest: it schedules onto the node labelled
# ingress-ready=true and tolerates the control-plane taint, which is what
# k8s/kind-config.yaml sets up.
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

echo "==> waiting for the controller to be ready"
kubectl -n "$CONTROLLER_NS" wait --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=300s

# The NetworkPolicy matches the controller by namespace label. Kubernetes sets
# kubernetes.io/metadata.name automatically on every namespace, but assert it
# rather than assume: without the label the policy silently blocks the
# controller and a correct setup returns 502 with nothing to explain why.
echo "==> confirming the controller namespace carries the label the NetworkPolicy matches"
kubectl get namespace "$CONTROLLER_NS" \
  -o jsonpath='{.metadata.labels.kubernetes\.io/metadata\.name}' | grep -qx "$CONTROLLER_NS"

echo "==> generating the local certificate"
TLS_HOST="$HOST" NS="$NS" ./scripts/gen_tls_secret.sh

echo "==> applying the Ingress"
kubectl apply -k k8s/

# nginx holds the certificate it was given at load time.
echo "==> restarting the controller so it loads the new certificate"
kubectl -n "$CONTROLLER_NS" rollout restart deployment ingress-nginx-controller
kubectl -n "$CONTROLLER_NS" rollout status deployment ingress-nginx-controller --timeout=300s

echo
echo "Ready. The platform is reachable over HTTPS at https://$HOST"
echo
echo "If the cluster was created from k8s/kind-config.yaml, host port 443 maps"
echo "into the node and this works once $HOST resolves to 127.0.0.1:"
echo "  curl --cacert <the certificate> https://$HOST/health"
echo
echo "Otherwise, forward the controller instead:"
echo "  kubectl -n $CONTROLLER_NS port-forward svc/ingress-nginx-controller 8443:443"
echo "  curl -k --resolve $HOST:8443:127.0.0.1 https://$HOST:8443/health"
echo
echo "The certificate is self-signed, so a client must be told to trust it."
echo "See docs/kubernetes.md. -k skips that check and is fine for a local demo;"
echo "it is not fine anywhere a credential is being sent to a real host."
