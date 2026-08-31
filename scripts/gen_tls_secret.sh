#!/usr/bin/env bash
# Create the TLS Secret the Ingress terminates with.
#
# Self-signed, generated on demand, and never written into the repository. The
# key and certificate are produced in a directory that only this user can read,
# handed to kubectl, and deleted -- so the private key exists on disk for the
# length of one command and nowhere the working tree can see it. .gitignore
# refuses *.key, *.pem and *.crt by extension as a second line of defence.
#
# Idempotent: re-running replaces the Secret and rolls the Deployment, because
# nginx caches the certificate it was given and a replaced Secret is not
# noticed by a running controller on its own.
#
# This is transport security only. It is not authentication -- a request over
# HTTPS without a bearer credential still gets 401, which is the V2.6 gate
# doing its job. The two are separate on purpose.
set -euo pipefail

NS="${NS:-agent-platform}"
HOST="${TLS_HOST:-agent-platform.local}"
DAYS="${TLS_DAYS:-365}"
SECRET=agent-platform-tls

command -v openssl >/dev/null || {
  echo "openssl is required to generate the local certificate" >&2
  exit 1
}

workdir="$(mktemp -d)"
# Even on failure. A private key surviving in /tmp is the thing this script
# exists to avoid.
trap 'rm -rf "$workdir"' EXIT
chmod 700 "$workdir"

# A SAN is mandatory: modern clients ignore the legacy CN entirely, so a
# certificate without subjectAltName is rejected by curl and by every browser
# no matter how it was generated.
# MSYS2_ARG_CONV_EXCL: on Git Bash an argument beginning with `/` is treated
# as a Unix path and rewritten, so `-subj "/CN=host/O=org"` reaches openssl as
# `C:/Program Files/Git/CN=host/O=org` and is rejected as a malformed subject.
#
# Scoped to `/CN=` rather than `*`. Excluding everything also stops `/tmp/...`
# being translated, and a native Windows openssl cannot open a path it was
# handed in Unix form -- which trades one failure for another. The variable
# means nothing on Linux, so one line covers both platforms.
#
# There is deliberately no `2>/dev/null` here any more. It used to hide exactly
# this failure: the script died on `set -e` with the diagnosis discarded,
# leaving a controller installed, no certificate, and nothing to explain why.
MSYS2_ARG_CONV_EXCL='/CN=' openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$workdir/tls.key" \
  -out "$workdir/tls.crt" \
  -days "$DAYS" \
  -subj "/CN=$HOST/O=agent-platform local development" \
  -addext "subjectAltName=DNS:$HOST,DNS:localhost,IP:127.0.0.1" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth"

kubectl -n "$NS" delete secret "$SECRET" --ignore-not-found >/dev/null
kubectl -n "$NS" create secret tls "$SECRET" \
  --cert="$workdir/tls.crt" \
  --key="$workdir/tls.key"

# The fingerprint, so an operator can confirm the certificate a client is being
# asked to trust is the one just created. A public value: it identifies the
# certificate and reveals nothing about the key.
echo
echo "certificate for: $HOST  (valid $DAYS days, self-signed)"
MSYS2_ARG_CONV_EXCL='/CN=' openssl x509 -in "$workdir/tls.crt" -noout -fingerprint -sha256 | sed 's/^/  /'
echo
echo "The private key was never written outside a temporary directory and is"
echo "being deleted now. To reach the platform, see docs/kubernetes.md."
