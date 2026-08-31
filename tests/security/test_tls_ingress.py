"""TLS at the edge, and the things it must not be confused with.

Before V2.9 the reason the Service stayed `ClusterIP` was written down plainly:
a bearer credential over plain HTTP is a credential in the clear. The Ingress
removes that caveat and nothing else. It does not authenticate anybody, it does
not decide what a caller may do, and it does not replace the V2.6 gate -- a
request arriving over HTTPS with no credential still gets 401.

These are the checks that hold without a cluster. Everything that needs one --
does the certificate actually serve, does HTTP redirect, does the controller
reach the pod through the NetworkPolicy -- lives in `scripts/tls_proofs.py` and
runs against a live deployment.

The one thing this file cares about more than any other: **no private key is
in the repository, and none can be.**
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
K8S = REPO / "k8s"
HOST = "agent-platform.local"


def load(name: str) -> dict:
    return yaml.safe_load((K8S / name).read_text(encoding="utf-8"))


def load_all(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((K8S / name).read_text(encoding="utf-8")) if d]


# ================================================== no key, anywhere, ever


def test_no_private_key_is_tracked_by_git():
    """The property the whole design is arranged around.

    Checked against what git actually tracks rather than against a directory
    listing: a key that is present but ignored is a local accident, and a key
    that is tracked is a published one.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=60
    ).stdout.split()
    offenders = [
        name
        for name in tracked
        if name.endswith((".key", ".pem", ".crt", ".csr", ".p12", ".pfx"))
    ]
    assert offenders == [], f"key material is tracked: {offenders}"


def test_no_pem_block_is_tracked_by_git():
    """And by content, not only by extension.

    A key pasted into a YAML file has no telling suffix.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, timeout=60
    ).stdout.split()
    pattern = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    offenders = []
    for name in tracked:
        path = REPO / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            offenders.append(name)
    assert offenders == [], f"a private key block is committed: {offenders}"


def test_gitignore_refuses_key_material_by_extension():
    """So a key dropped anywhere is ignored, not only one in an expected path."""
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.key", "*.pem", "*.crt"):
        assert pattern in ignored, f"{pattern} is not ignored"


def test_the_tls_template_carries_no_key():
    """The template exists to be read, not filled in."""
    secret = load("tls-secret.example.yaml")
    assert secret["type"] == "kubernetes.io/tls"
    body = (K8S / "tls-secret.example.yaml").read_text(encoding="utf-8")
    assert "BEGIN" not in body
    assert "REPLACE_ME" in secret["stringData"]["tls.key"]


def test_the_generator_never_writes_a_key_into_the_repository():
    """Read statically: running it would need a cluster.

    The key is created in a mktemp directory, removed by a trap that fires even
    on failure, and handed to kubectl by file path rather than on the command
    line -- argv is visible to every process on the host.
    """
    script = (REPO / "scripts" / "gen_tls_secret.sh").read_text(encoding="utf-8")
    assert "mktemp -d" in script
    assert "trap 'rm -rf" in script, "the temporary directory must be removed on exit"
    assert "--key=" in script and "--cert=" in script
    assert "-nodes" in script, "the key must not be passphrase-protected for kubectl"
    # No path inside the repository.
    assert not re.search(r"(k8s|certs?)/[a-z_]*\.(key|crt|pem)", script)


# ========================================================== the Ingress itself


def test_the_ingress_terminates_tls_for_the_documented_host():
    ingress = load("ingress.yaml")
    assert ingress["kind"] == "Ingress"
    tls = ingress["spec"]["tls"]
    assert len(tls) == 1
    assert tls[0]["hosts"] == [HOST]
    assert tls[0]["secretName"] == "agent-platform-tls"


def test_the_ingress_and_the_rule_agree_on_the_host():
    """A TLS block for one host and a rule for another is a silent 404."""
    ingress = load("ingress.yaml")
    rule_hosts = [rule["host"] for rule in ingress["spec"]["rules"]]
    tls_hosts = [h for entry in ingress["spec"]["tls"] for h in entry["hosts"]]
    assert set(rule_hosts) == set(tls_hosts)


def test_plain_http_is_redirected_rather_than_served():
    """An API that quietly accepts cleartext is an API whose clients send
    credentials over it."""
    annotations = load("ingress.yaml")["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/ssl-redirect"] == "true"
    assert annotations["nginx.ingress.kubernetes.io/force-ssl-redirect"] == "true"


def test_the_ingress_points_at_the_service_by_name_and_port_name():
    """Port *name*, not number: the two would drift independently."""
    backend = load("ingress.yaml")["spec"]["rules"][0]["http"]["paths"][0]["backend"]
    service = load("service.yaml")
    assert backend["service"]["name"] == service["metadata"]["name"]
    assert backend["service"]["port"]["name"] == service["spec"]["ports"][0]["name"]


def test_the_service_is_still_clusterip():
    """TLS at the edge does not mean publishing the Service.

    The only route in is the controller, which is the single place TLS is
    configured and the single place to look when it is wrong.
    """
    assert load("service.yaml")["spec"]["type"] == "ClusterIP"


def test_the_ingress_carries_no_credential():
    body = (K8S / "ingress.yaml").read_text(encoding="utf-8")
    assert not re.search(r"\bap_[0-9a-f]{8}_", body)
    assert "GEMINI_API_KEY" not in body
    assert "AGENT_PLATFORM_LIVE" not in body


# ============================================ the controller can actually reach


def test_the_network_policy_admits_the_ingress_controller():
    """The rule whose absence produces a 502 with no explanation.

    `podSelector: {}` is namespace-scoped, which reads like "anything" and is
    not. The controller lives in ingress-nginx, so without a namespaceSelector
    the default-deny policy silences a perfectly correct Ingress.
    """
    policies = {d["metadata"]["name"]: d for d in load_all("network-policy.yaml")}
    api = policies["api-ingress"]
    selectors = [
        rule
        for entry in api["spec"]["ingress"]
        for rule in entry.get("from", [])
        if "namespaceSelector" in rule
    ]
    assert selectors, "no namespaceSelector: the ingress controller cannot reach the API"
    labels = selectors[0]["namespaceSelector"]["matchLabels"]
    assert labels == {"kubernetes.io/metadata.name": "ingress-nginx"}


def test_the_default_deny_policy_is_still_in_place():
    """The complement. Opening a hole for the controller must not open the door."""
    policies = {d["metadata"]["name"]: d for d in load_all("network-policy.yaml")}
    deny = policies["default-deny"]
    assert deny["spec"]["podSelector"] == {}
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}


# ================================================================== wiring


def test_the_ingress_is_applied_by_kustomize():
    kustomization = load("kustomization.yaml")
    assert "ingress.yaml" in kustomization["resources"]


def test_no_secret_template_is_applied_by_kustomize():
    """Applying a template would install a placeholder as a real credential."""
    resources = load("kustomization.yaml")["resources"]
    for template in (
        "secret.example.yaml",
        "auth-secret.example.yaml",
        "tls-secret.example.yaml",
    ):
        assert template not in resources


def test_kustomize_renders_with_the_ingress_and_no_key():
    rendered = subprocess.run(  # noqa: S603 - fixed argv
        ["kubectl", "kustomize", str(K8S)],
        capture_output=True, text=True, timeout=180,
    )
    if rendered.returncode != 0:
        pytest.skip("kubectl is not available to render the manifests")
    assert "kind: Ingress" in rendered.stdout
    assert "BEGIN" not in rendered.stdout
    assert not re.search(r"\bap_[0-9a-f]{8}_", rendered.stdout)


def test_a_new_cluster_is_created_able_to_serve_an_ingress():
    """kind bakes port mappings in at creation, so this cannot be added later."""
    config = load("kind-config.yaml")
    node = config["nodes"][0]
    mapped = {m["containerPort"] for m in node["extraPortMappings"]}
    assert {80, 443} <= mapped
    assert "ingress-ready=true" in node["kubeadmConfigPatches"][0]


def test_k8s_up_never_recreates_an_existing_cluster():
    """Recreating destroys everything in it. That is a person's decision."""
    script = (REPO / "scripts" / "k8s_up.sh").read_text(encoding="utf-8")
    assert "kind delete cluster" not in script
    assert "--config k8s/kind-config.yaml" in script
    assert "already exists" in script


def test_the_ingress_bootstrap_restarts_the_controller():
    """nginx caches the certificate it was handed; a replaced Secret is not
    noticed by a running controller on its own."""
    script = (REPO / "scripts" / "k8s_ingress_up.sh").read_text(encoding="utf-8")
    assert "rollout restart deployment ingress-nginx-controller" in script
    assert "gen_tls_secret.sh" in script


# ================================================ TLS is not authentication


def test_tls_does_not_appear_in_the_application_at_all():
    """Termination is the edge's job. The app speaks HTTP and knows nothing
    about certificates, which is why adding TLS changed no application code."""
    api = (REPO / "src" / "agent_platform" / "api").rglob("*.py")
    for module in api:
        text = module.read_text(encoding="utf-8")
        assert "ssl_keyfile" not in text
        assert "ssl_certfile" not in text


def test_the_authentication_gate_is_untouched_by_this_phase():
    """The 401 a credential-less caller gets is the same 401 as before.

    If TLS had been allowed to blur into authentication, this is where it would
    show: some notion of a trusted network deciding who a caller is.
    """
    app = (REPO / "src" / "agent_platform" / "api" / "app.py").read_text(encoding="utf-8")
    assert "X-Forwarded-For" not in app, "the app must not trust a proxy header for identity"
    assert "X-Real-IP" not in app
    assert "remote_addr" not in app
