"""The image, exercised as an image.

These tests build the real Dockerfile, run the real container, and talk to it
over real HTTP. Nothing here is a unit test of a Dockerfile string: a Dockerfile
that "looks right" is not evidence, and this file exists because the first
hardening pass of this image looked right and was broken.

What it caught: adding ``--chmod=0444`` to the ``COPY`` of the vector index also
applied that mode to the parent directory BuildKit created for it, producing
``dr--r--r--``. Readable, but with no execute bit the directory cannot be
traversed, so the index became unreachable to every user -- including the one
that needs it. Every "does the file exist" style check still passed. Only
loading the index for real failed.

So the assertions below open the index rather than stat it, run a request rather
than curl the health endpoint alone, and read the effective uid rather than the
``USER`` line.

Skipped cleanly wherever Docker is not available, so the suite stays portable.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IMAGE = "agent-platform:pytest"
DIGEST = "db512de8207f751e"
FINGERPRINT = "2149ed589721bee7"


def _docker_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


needs_docker = pytest.mark.skipif(
    not _docker_available(), reason="docker daemon is not available"
)

pytestmark = [needs_docker, pytest.mark.docker]


def _run(
    *args: str, timeout: int = 900, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv built from literals
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **env} if env else None,
    )


#: A credential table that is syntactically valid and cryptographically useless:
#: the digest is a constant with no known preimage, so nothing authenticates
#: against it. Enough to let Compose render; not a credential.
_INERT_KEYS = "0000000a compose-render runs:write " + "0" * 64


def _compose(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Drive Compose against an empty env file.

    ``--env-file`` with an empty file replaces the project's ``.env`` as the
    substitution source, so these tests see exactly the variables they set and
    are not affected by whatever the developer happens to have configured
    locally.
    """
    handle, empty = tempfile.mkstemp(suffix=".env")
    os.close(handle)
    return _run(
        "compose",
        "--env-file",
        empty,
        "-f",
        str(REPO / "compose.yaml"),
        *args,
        timeout=120,
        env=env,
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def image() -> str:
    """Build the image from the real Dockerfile.

    Layer caching makes the repeat cost small; a cold build is the price of
    testing the artefact that actually ships.
    """
    built = _run("build", "-t", IMAGE, str(REPO))
    if built.returncode != 0:
        pytest.fail(f"docker build failed:\n{built.stdout[-2000:]}\n{built.stderr[-2000:]}")
    return IMAGE


class _Container(dict):
    """The running container, with a repr that does not print its credentials.

    A failing assertion renders every fixture in the traceback. When this was a
    plain dict, one failure printed four working tokens into pytest output.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<container {self['name']} at {self['base']}>"

    __str__ = __repr__


@pytest.fixture(scope="module")
def credentials() -> dict[str, str]:
    """Credentials for the container, minted here.

    What reaches the container is the digest table, which is what
    ``API_AUTH_KEYS`` holds -- so the secret itself never appears in a docker
    argv, in the image, or in this file. The tokens stay in this process.
    """
    from agent_platform.security.api_auth import issue_token

    tokens: dict[str, str] = {}
    lines: list[str] = []
    for name, scopes in (
        ("operator", "runs:write,confirm:write"),
        ("scraper", "metrics:read"),
    ):
        key_id, token, digest = issue_token()
        tokens[name] = token
        lines.append(f"{key_id} {name} {scopes} {digest}")
    tokens["keys"] = "\n".join(lines)
    return tokens


@pytest.fixture(scope="module")
def container(image: str, credentials: dict[str, str]):
    """A running container, torn down whatever happens."""
    port = _free_port()
    name = f"ap-pytest-{port}"
    _run("rm", "-f", name)

    started = _run(
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"{port}:8000",
        "-e",
        f"API_AUTH_KEYS={credentials['keys']}",
        image,
    )
    if started.returncode != 0:
        pytest.fail(f"docker run failed: {started.stderr[-1000:]}")

    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        last: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception as exc:
                last = exc
                time.sleep(0.5)
        else:
            logs = _run("logs", name)
            pytest.fail(f"container never became healthy ({last}); logs:\n{logs.stdout[-2000:]}")

        yield _Container(name=name, base=base, tokens=credentials)
    finally:
        _run("rm", "-f", name)


def _exec_python(name: str, script: str) -> str:
    proc = _run("exec", name, "python", "-c", script, timeout=180)
    assert proc.returncode == 0, f"in-container python failed:\n{proc.stderr[-1500:]}"
    return proc.stdout.strip()


def _post(base: str, path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(base: str, path: str, token: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{path}",
        headers={"authorization": f"Bearer {token}"} if token else {},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ============================================================ identity


def test_the_container_does_not_run_as_root(container):
    """``USER`` in a Dockerfile is a claim; the effective uid is the fact."""
    proc = _run("exec", container["name"], "id", "-u")
    assert proc.returncode == 0
    uid = int(proc.stdout.strip())
    assert uid == 10001, f"running as uid {uid}"
    assert uid != 0


def test_the_application_tree_is_not_writable_by_the_app_user(container):
    """A writable install tree means a compromised request handler can rewrite
    the code that will run on the next one."""
    for path in ("/usr/local/lib/python3.12/site-packages", "/opt/agent-platform"):
        proc = _run("exec", container["name"], "sh", "-c", f"touch {path}/probe 2>&1")
        assert proc.returncode != 0, f"{path} is writable by the app user"


def test_exactly_one_directory_is_writable(container):
    """SQLite has to write somewhere, and that somewhere must be deliberate."""
    proc = _run(
        "exec", container["name"], "sh", "-c",
        "touch /var/lib/agent-platform/probe && echo ok && rm /var/lib/agent-platform/probe",
    )
    assert proc.stdout.strip() == "ok"


# ============================================================ secrets


def test_no_env_file_reached_the_image(container):
    proc = _run(
        "exec", container["name"], "sh", "-c",
        "find / -name '.env' -not -path '/proc/*' -not -path '/sys/*' 2>/dev/null | head",
    )
    assert proc.stdout.strip() == "", f"an .env file is in the image: {proc.stdout}"


def test_no_provider_credential_is_configured_in_the_image(container):
    """The image must run the stub by default. A key baked in would also mean a
    key in a layer, readable by anyone who pulls it."""
    proc = _run("exec", container["name"], "env")
    names = {line.split("=", 1)[0] for line in proc.stdout.splitlines() if "=" in line}
    for forbidden in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"
    ):
        assert forbidden not in names, f"{forbidden} is set in the image"


def test_compose_cannot_be_talked_into_live_mode_by_a_dotfile():
    """Compose must resolve to the offline stub, whatever is lying around.

    This is a regression test for a real incident during this phase. The file
    said ``GEMINI_API_KEY: "${GEMINI_API_KEY:-}"``, meaning "pass one through
    only if the operator exported it". Compose reads the project's ``.env``
    automatically for substitution, found the developer's real key there, and
    ``docker compose up`` started in LIVE mode and spent real quota -- while the
    comment directly above the line asserted it ran offline.

    ``docker compose config`` renders the fully substituted configuration, so
    this asserts against what Compose will actually do rather than against what
    the YAML appears to say.
    """
    proc = _compose("config", env={"API_AUTH_KEYS": _INERT_KEYS})
    assert proc.returncode == 0, proc.stderr

    rendered = proc.stdout
    key_lines = [
        line.strip()
        for line in rendered.splitlines()
        if line.strip().startswith("GEMINI_API_KEY:")
    ]
    assert key_lines, "compose no longer pins GEMINI_API_KEY; it could inherit one"
    for line in key_lines:
        value = line.split(":", 1)[1].strip().strip("\"'")
        assert value == "", (
            "compose resolved a non-empty GEMINI_API_KEY, so `docker compose up` "
            "would start in LIVE mode and spend real quota"
        )


def test_the_real_developer_key_is_absent_from_the_image(image):
    """The local ``.env`` holds a real key. Prove that exact value is nowhere in
    the image rather than trusting ``.dockerignore`` to have worked.

    The needle never reaches a command line, an environment variable or an
    assertion message. An earlier version of this test passed it as a ``grep``
    argument, and the first timeout printed the developer's live API key into
    the pytest traceback -- a test for secret leakage that leaked the secret.
    The filesystem is streamed here and matched in memory instead, and failures
    report only *where* a match was found.
    """
    env_file = REPO / ".env"
    if not env_file.exists():
        pytest.skip("no local .env to check against")

    needle = ""
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("GEMINI_API_KEY") and "=" in line:
            needle = line.split("=", 1)[1].strip().strip("\"'")
    if len(needle) < 20:
        pytest.skip("no usable key in .env to search for")

    target = needle.encode()
    created = _run("create", image, timeout=120)
    assert created.returncode == 0, created.stderr
    container_id = created.stdout.strip()

    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            ["docker", "export", container_id], stdout=subprocess.PIPE
        )
        assert proc.stdout is not None
        found = False
        carry = b""
        try:
            while chunk := proc.stdout.read(1 << 20):
                if target in carry + chunk:
                    found = True
                    break
                # Keep an overlap so a match spanning two chunks is still seen.
                carry = chunk[-len(target) :]
        finally:
            proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=60)
    finally:
        _run("rm", "-f", container_id, timeout=120)

    assert not found, "the developer's GEMINI_API_KEY is present in the image filesystem"


# ============================================================ HTTP surface


def test_health_answers(container):
    status, body = _get(container["base"], "/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_ready_reports_the_checks_it_made(container):
    status, body = _get(container["base"], "/ready", container["tokens"]["scraper"])
    assert status == 200
    assert body["ready"] is True
    assert body["checks"] == {"repository": True, "registry": True}
    assert body["provider"] == "stub", "the image must default to the offline stub"


def test_a_record_lookup_answers_with_its_data(container):
    status, body = _post(
        container["base"], "/runs", {"input": "What is the status of order ORD-1001?"},
        container["tokens"]["operator"],
    )
    assert status == 200
    assert body["status"] == "success"
    assert "ORD-1001" in body["response"]
    assert "shipped" in body["response"]
    assert body["provider"] == "stub"


def test_a_knowledge_base_question_answers_from_the_corpus(container):
    status, body = _post(
        container["base"],
        "/runs",
        {"input": "What is the refund policy?"},
        container["tokens"]["operator"],
    )
    assert status == 200
    assert body["status"] == "success"
    assert "Refund policy" in body["response"]


def test_a_policy_denial_still_denies_inside_the_container(container):
    status, body = _post(
        container["base"], "/runs", {"input": "Delete customer record CUS-2001"},
        container["tokens"]["operator"],
    )
    assert status == 200
    assert body["status"] == "blocked"
    assert body["policy_decision"]["decision"] == "deny"


def test_an_invalid_body_is_rejected(container):
    status, body = _post(container["base"], "/runs", {"nope": 1}, container["tokens"]["operator"])
    assert status == 400
    assert body["error"] == "bad_request"


def test_the_confirmation_flow_works_end_to_end(container):
    """Suspend, approve, and prove the approval is single-use."""
    status, created = _post(
        container["base"], "/runs", {"input": "Send a message to customer CUS-2001"},
        container["tokens"]["operator"],
    )
    assert status == 200
    assert created["status"] == "awaiting_confirmation"
    request_id = created["request_id"]

    status, resumed = _post(
        container["base"], f"/runs/{request_id}/confirm", {"approved": True},
        container["tokens"]["operator"],
    )
    assert status == 200
    assert resumed["status"] == "success"

    status, _ = _post(
        container["base"],
        f"/runs/{request_id}/confirm",
        {"approved": True},
        container["tokens"]["operator"],
    )
    assert status == 404, "a consumed confirmation was resumable a second time"


# ============================================== the installed package itself


def test_the_package_is_installed_not_copied(container):
    """A copied checkout would mask every packaging mistake. The image must run
    from site-packages, with no source tree present."""
    out = _exec_python(
        container["name"],
        "import pathlib,agent_platform,json;"
        "p=pathlib.Path(agent_platform.__file__).parent;"
        "print(json.dumps({'dir':str(p),"
        "'app':[x.name for x in pathlib.Path('/app').iterdir()]}))",
    )
    info = json.loads(out)
    assert "site-packages" in info["dir"], f"package is not installed: {info['dir']}"
    assert info["app"] == [], f"a checkout leaked into the image: {info['app']}"


def test_package_data_survived_the_wheel(container):
    """``schema.sql`` and the golden datasets are loaded at runtime by path. A
    file left out of the wheel breaks only once installed."""
    out = _exec_python(
        container["name"],
        "import pathlib,agent_platform,json;"
        "p=pathlib.Path(agent_platform.__file__).parent;"
        "print(json.dumps({r:(p/r).exists() for r in ["
        "'persistence/schema.sql','evaluation/datasets/normal.json',"
        "'evaluation/datasets/adversarial.json','evaluation/datasets/tool_use.json']}))",
    )
    missing = [k for k, v in json.loads(out).items() if not v]
    assert not missing, f"missing package data in the installed wheel: {missing}"


def test_the_vector_index_actually_loads_in_the_container(container):
    """The regression this file was written for.

    Not ``exists()`` -- that returned True while the directory was untraversable
    and every read failed. This opens the index and reads its contents.
    """
    out = _exec_python(
        container["name"],
        "import json;"
        "from agent_platform.retrieval import strategy;"
        "from agent_platform.retrieval.model import build_chunks, corpus_fingerprint;"
        "i=strategy._load_index();"
        "print(json.dumps({'path':str(strategy.resolve_index_path()),"
        "'vectors':len(i.doc_ids),"
        "'fingerprint':i.metadata.corpus_fingerprint,"
        "'expected':corpus_fingerprint(build_chunks())}))",
    )
    info = json.loads(out)
    assert info["vectors"] == 12, f"expected 12 vectors, got {info['vectors']}"
    assert info["fingerprint"] == info["expected"] == FINGERPRINT
    assert info["path"] == "/opt/agent-platform/kb_vectors.db"


def test_the_index_is_readable_but_not_writable(container):
    proc = _run(
        "exec", container["name"], "sh", "-c",
        "test -r /opt/agent-platform/kb_vectors.db && echo readable; "
        "touch /opt/agent-platform/kb_vectors.db 2>/dev/null && echo WRITABLE",
    )
    assert "readable" in proc.stdout
    assert "WRITABLE" not in proc.stdout


def test_the_invariants_hold_inside_the_container(container):
    out = _exec_python(
        container["name"],
        "import json;"
        "from agent_platform.tools.dataset import dataset_digest;"
        "from agent_platform.tools.fake_tools import KB_ARTICLES;"
        "from agent_platform.retrieval.strategy import MIN_COSINE_SIMILARITY;"
        "print(json.dumps({'digest':dataset_digest(),'kb':len(KB_ARTICLES),"
        "'threshold':MIN_COSINE_SIMILARITY}))",
    )
    info = json.loads(out)
    assert info["digest"] == DIGEST
    assert info["kb"] == 12
    assert info["threshold"] == 0.67


def test_the_stub_needs_no_network_or_key(container):
    """Demo traffic must work with no provider configured at all."""
    out = _exec_python(
        container["name"],
        "import os;print(repr(os.getenv('GEMINI_API_KEY')))",
    )
    assert out in {"None", "''"}, f"a key is configured: {out}"

    status, body = _post(
        container["base"],
        "/runs",
        {"input": "What is the refund policy?"},
        container["tokens"]["operator"],
    )
    assert status == 200
    assert body["provider"] == "stub"


# ============================================================ authentication


def test_the_image_refuses_to_serve_without_credentials(image):
    """Fail closed, proven against the artefact that ships.

    The strongest possible statement of the rule: an image started with no
    credential configuration does not come up unauthenticated, it does not come
    up at all. Asserting this against the Dockerfile text would prove nothing --
    the earlier hardening pass of this image looked right and was broken.
    """
    name = f"ap-pytest-noauth-{_free_port()}"
    _run("rm", "-f", name)
    try:
        started = _run("run", "-d", "--name", name, image)
        assert started.returncode == 0, started.stderr[-500:]

        deadline = time.time() + 30
        while time.time() < deadline:
            state = _run("inspect", "-f", "{{.State.Running}}", name).stdout.strip()
            if state == "false":
                break
            time.sleep(0.5)
        else:
            pytest.fail("the container kept running with no credentials configured")

        logs = _run("logs", name)
        assert "Refusing to serve" in logs.stdout + logs.stderr
    finally:
        _run("rm", "-f", name)


def test_the_running_container_refuses_an_anonymous_caller(container):
    status, body = _post(container["base"], "/runs", {"input": "What is the refund policy?"})
    assert status == 401
    assert body["error"] == "unauthorized"


def test_the_running_container_enforces_scopes(container):
    """The scraper may read metrics and may not spend money."""
    scraper = container["tokens"]["scraper"]
    status, _ = _post(container["base"], "/runs", {"input": "hello"}, scraper)
    assert status == 403


def test_the_health_probe_needs_no_credential(container):
    """The kubelet sends no header, so this must stay reachable."""
    status, body = _get(container["base"], "/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_compose_refuses_to_start_without_credentials():
    """Fail closed one layer earlier than the container.

    ``:?`` rather than ``:-``: with no credentials Compose brings nothing up and
    says what to set, instead of starting a container that crash-loops on the
    same problem where an operator has to read logs to find it.
    """
    proc = _compose("config")
    assert proc.returncode != 0
    assert "API_AUTH_KEYS" in proc.stderr


def test_compose_does_not_ship_a_working_credential():
    """No token, and no digest anybody could hold the preimage of.

    A demo credential published in a repository is a real credential in every
    deployment that copied the file and forgot.
    """
    text = (REPO / "compose.yaml").read_text(encoding="utf-8")
    assert "API_AUTH_MODE" not in text, "compose must not disable authentication"
    # The real token shape, not the substring: `cap_drop` contains "ap_".
    assert not re.search(r"ap_[0-9a-f]{8}_", text), "a token appears in compose.yaml"
    for line in text.splitlines():
        if "API_AUTH_KEYS" in line:
            assert "${" in line, "API_AUTH_KEYS must come from configuration, not be literal"
