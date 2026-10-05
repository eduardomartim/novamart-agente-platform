"""The deployment contracts, which no other test can see.

Everything the offline suite proves is about a process it starts itself. None of
it says anything about what a *builder* will install or which command a platform
will run, and those are exactly where this project's two deployments were broken
-- silently, in ways that only appear as a failed cold start or a service that
answers nothing.

Four defects are pinned here, each found by reading the builders' own
documentation rather than by running them:

* Vercel walks up from the entrypoint's directory and prefers `pyproject.toml`
  to `requirements*.txt` at every level. With only a root manifest it installs
  `[project.dependencies]`, which excludes Starlette, and the function cannot
  import.
* Railway prioritises a detected Dockerfile, and the root one builds the API.
* Railway's own builder would read the root `requirements.txt`, which is the API
  lock and has no Streamlit in it.
* Streamlit binds 8501 on localhost unless told otherwise, and Railway routes to
  `$PORT`.

These tests read manifests rather than start servers, which is the honest limit
of what can be checked without deploying. What they can do is fail when a change
would reintroduce one of the four, which is the failure mode that costs a
deployment rather than a test run.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "api"


def _requirement_names(path: Path, *, seen: set[Path] | None = None) -> set[str]:
    """Package names a requirements file resolves to, following `-r` includes.

    The includes are the point: the manifests deliberately point at one lock
    instead of restating it, so a test that read only the top file would be
    asserting about a pointer rather than about what gets installed.
    """
    seen = seen if seen is not None else set()
    resolved = path.resolve()
    if resolved in seen or not resolved.is_file():
        return set()
    seen.add(resolved)

    names: set[str] = set()
    for raw in resolved.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("--hash"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            target = line.split(None, 1)[1].strip()
            names |= _requirement_names(resolved.parent / target, seen=seen)
            continue
        match = re.match(r"^([A-Za-z0-9._-]+)\s*==", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


@pytest.fixture(scope="module")
def api_requirements() -> set[str]:
    return _requirement_names(API_DIR / "requirements.txt")


@pytest.fixture(scope="module")
def dashboard_requirements() -> set[str]:
    return _requirement_names(ROOT / "requirements-dashboard.txt")


# ===================================================== V1: the Vercel manifest


def test_the_api_carries_a_manifest_beside_its_entrypoint():
    """V1: without this, the walk reaches the root `pyproject.toml` and wins.

    Vercel checks `pyproject.toml`, `Pipfile.lock` and `Pipfile` before
    `requirements*.txt` *at each level* on the way up from the entrypoint. The
    file has to sit in `api/`, and `api/` must not gain a pyproject.toml of its
    own, or the same precedence applies one directory lower.
    """
    assert (API_DIR / "requirements.txt").is_file()
    assert not (API_DIR / "pyproject.toml").exists()
    assert not (API_DIR / "Pipfile").exists()
    assert not (API_DIR / "Pipfile.lock").exists()


def test_the_api_manifest_installs_what_the_entrypoint_imports(api_requirements):
    """The failure this replaces was `ModuleNotFoundError: starlette`."""
    for package in ("starlette", "uvicorn", "google-genai", "langgraph", "pydantic"):
        assert package in api_requirements, f"{package} is missing from the API manifest"


def test_the_api_manifest_carries_the_shared_state_drivers(api_requirements):
    """`REDIS_URL` and `DATABASE_URL` are set in production, so these are needed.

    Both live in optional extras, which is exactly why the root `pyproject.toml`
    was the wrong manifest: extras are not installed by default and the failure
    would have been a cold start that could not reach either backend.
    """
    assert "redis" in api_requirements
    assert "langgraph-checkpoint-redis" in api_requirements
    assert any(name.startswith("psycopg") for name in api_requirements)


def test_the_api_is_not_shipped_the_dashboard(api_requirements):
    """~170MB of Streamlit, pandas, altair and pyarrow the API never imports."""
    for package in ("streamlit", "pandas", "altair", "pyarrow"):
        assert package not in api_requirements, f"{package} does not belong in the API"


def test_the_api_manifest_points_at_the_lock_rather_than_restating_it():
    """One source of truth, so the image and the function cannot drift apart."""
    body = "\n".join(
        line.split("#", 1)[0].strip()
        for line in (API_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    )
    directives = [line for line in body.splitlines() if line]
    assert directives == ["-r ../requirements-api.txt"], directives


def test_the_lock_the_api_resolves_to_is_still_hashed():
    """The pins reached through that pointer are the verified ones."""
    lock = (ROOT / "requirements-api.txt").read_text(encoding="utf-8")
    assert lock.count("--hash=sha256:") > 500
    assert not re.search(r"^[A-Za-z0-9._-]+[<>~!]", lock, re.MULTILINE), (
        "an unpinned requirement would break hash-checking for the whole install"
    )


def test_vercel_routes_everything_to_the_entrypoint():
    import json

    vercel = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert vercel["rewrites"] == [{"source": "/(.*)", "destination": "/api/index"}]
    assert "api/index.py" in vercel["functions"]


def test_the_entrypoint_exports_a_module_level_app():
    """Vercel serves the module-level `app`; a factory or a nested one is invisible.

    Parsed rather than imported: importing it builds a platform, and this is a
    statement about the module's shape.
    """
    tree = ast.parse((API_DIR / "index.py").read_text(encoding="utf-8"))
    assigned = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "app" in assigned


# ============================================== R1/R3: which image, which command


@pytest.fixture(scope="module")
def railway() -> dict:
    path = ROOT / "railway.toml"
    assert path.is_file(), "Railway would fall back to detecting the API Dockerfile"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_railway_names_a_builder_rather_than_letting_detection_choose(railway):
    """R1: a detected Dockerfile wins, and the detected one builds the API."""
    assert railway["build"]["builder"].lower() == "dockerfile"


def test_railway_does_not_override_the_start_command(railway):
    """R3: overriding a Dockerfile CMD from Railway uses exec form.

    Exec form does not expand variables, so a `startCommand` mentioning `$PORT`
    would pass the four characters to Streamlit and the service would bind
    nothing. The image's shell-form CMD is what makes the expansion happen.
    """
    assert "startCommand" not in railway.get("deploy", {})


def _instructions(path: Path) -> str:
    """A Dockerfile's executable content, without its comments.

    Both files explain in prose what the *other* one does, which is worth having
    and would make any test that grepped the raw text match its own commentary.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


def test_the_dashboard_image_is_separate_from_the_api_image():
    """R1: two images, each with one job. Neither may become the other."""
    api = _instructions(ROOT / "Dockerfile")
    dashboard = _instructions(ROOT / "Dockerfile.dashboard")

    assert "agent_platform.api" in api
    assert "streamlit run" not in api, "the API image must not learn to be the dashboard"
    assert "agent_platform.api" not in dashboard, "the dashboard image starts the API"
    assert "streamlit run" in dashboard


def test_the_dashboard_start_command_is_reachable_from_outside():
    """R3: the three flags a Railway service cannot run without.

    `$PORT` because Railway routes there and injects it at run time; `0.0.0.0`
    because localhost is unreachable from outside the container; headless
    because Streamlit otherwise tries to open a browser and prompts for an
    email on first run.
    """
    dockerfile = (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    command = dockerfile[dockerfile.index("CMD ") :]

    assert "${PORT" in command, "the port must come from Railway, not a literal"
    assert "--server.address=0.0.0.0" in command
    assert "--server.headless=true" in command
    # Shell form, not exec form: `CMD ["streamlit", ...]` would pass the literal
    # characters `${PORT}` to Streamlit rather than the port.
    assert not re.search(r"CMD\s*\[", command), "exec form does not expand $PORT"


def test_the_dashboard_image_carries_the_hardening_config():
    """`showErrorDetails` and `toolbarMode` are security settings.

    An image built without `.streamlit/` renders tracebacks, and their absolute
    paths, into a visitor's browser -- the F9 finding, undone by a COPY nobody
    noticed was missing.
    """
    dockerfile = (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    assert ".streamlit/" in dockerfile


def test_the_dashboard_image_preserves_the_layout_the_app_resolves_paths_against():
    """`app.py` puts `<parent>/../src` on sys.path; retrieval reads `<repo>/data`.

    Both are computed from `__file__`, so the container has to keep `src/` and
    `dashboard/` as siblings or the app cannot import its own package.
    """
    dockerfile = (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    assert "/app/src/" in dockerfile
    assert "/app/dashboard/" in dockerfile

    bootstrap = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert 'parents[1] / "src"' in bootstrap, (
        "the app changed how it finds its package; the image layout must follow"
    )


# ============================================= R2: the dashboard's own manifest


def test_the_dashboard_manifest_installs_streamlit(dashboard_requirements):
    """R2: the root `requirements.txt` is the API lock and has none of this."""
    for package in ("streamlit", "pandas", "altair"):
        assert package in dashboard_requirements, f"{package} is missing"


def test_the_dashboard_manifest_carries_the_platform_it_runs(dashboard_requirements):
    """The dashboard is not an API client -- it runs the platform in-process."""
    for package in ("langgraph", "google-genai", "pydantic", "redis"):
        assert package in dashboard_requirements
    assert any(name.startswith("psycopg") for name in dashboard_requirements)


def test_every_dashboard_requirement_is_pinned():
    """No floating versions, whatever else is true about hashes."""
    body = (ROOT / "requirements-dashboard.txt").read_text(encoding="utf-8")
    stripped = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    floating = re.findall(r"^\s*([A-Za-z0-9._-]+\s*[<>~!]=?[^\n]*)$", stripped, re.MULTILINE)
    assert not floating, f"unpinned dashboard requirements: {floating}"


def test_the_dashboard_installs_without_requiring_hashes():
    """Stated where it is done, because the guarantee differs from the API's.

    pip's hash checking is all-or-nothing, so a file mixing the hashed lock with
    unhashed pins cannot install under `--require-hashes`. The flag is the
    honest consequence, not an oversight, and this test exists so that removing
    it fails here rather than in a build log.
    """
    dockerfile = (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    assert "--no-require-hashes" in dockerfile
    assert "requirements-dashboard.txt" in dockerfile


def test_the_two_runtimes_agree_on_every_shared_pin(api_requirements, dashboard_requirements):
    """One platform, two deployments, one set of versions.

    They overlap almost entirely and differ only at the edges -- Streamlit on one
    side, Starlette on the other. Version skew between them would mean the same
    request behaving differently depending on which door it came through, which
    is the failure that made both manifests point at one lock.
    """
    api_pins = _pins(ROOT / "api" / "requirements.txt")
    dashboard_pins = _pins(ROOT / "requirements-dashboard.txt")
    shared = set(api_pins) & set(dashboard_pins)

    assert len(shared) > 50, "the two runtimes should share the platform's dependencies"
    disagreements = {
        name: (api_pins[name], dashboard_pins[name])
        for name in shared
        if api_pins[name] != dashboard_pins[name]
    }
    assert not disagreements, f"version skew between the deployments: {disagreements}"


def _pins(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    seen = seen if seen is not None else set()
    resolved = path.resolve()
    if resolved in seen or not resolved.is_file():
        return {}
    seen.add(resolved)

    pins: dict[str, str] = {}
    for raw in resolved.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("--hash"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            pins.update(_pins(resolved.parent / line.split(None, 1)[1].strip(), seen))
            continue
        match = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;\\]+)", line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


# ================================== the architecture this change must not alter


def test_the_dashboard_still_runs_the_platform_in_process():
    """No Railway -> Vercel arrow was introduced.

    The dashboard imports `AgentPlatform` and calls it. Turning it into an HTTP
    client of the API would put a credential in the dashboard, add a hop the
    architecture does not have, and is the change this task was told not to make.
    """
    source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if node.module == "agent_platform.platform"
    }
    assert "AgentPlatform" in imported

    for forbidden in ("httpx", "requests", "urllib.request", "aiohttp"):
        assert not re.search(rf"^\s*(import|from)\s+{re.escape(forbidden)}\b", source,
                             re.MULTILINE), f"the dashboard gained an HTTP client ({forbidden})"


def test_the_dashboard_needs_no_api_credential():
    """It never calls the API, so it must never be configured to authenticate.

    An `API_AUTH_KEYS` on the dashboard service would be a credential deployed
    somewhere with no use for it, which is the kind of thing that stops being
    unused later.
    """
    for path in sorted((ROOT / "dashboard").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "API_AUTH_KEYS" not in source, f"{path.name} reads an API credential"
    assert "API_AUTH_KEYS" not in (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    assert "API_AUTH_KEYS" not in (ROOT / "railway.toml").read_text(encoding="utf-8")


def test_no_deployment_file_carries_a_secret_value():
    """Names, never values. The one exception would be the one that leaks."""
    suspicious = re.compile(
        r"(AIza[0-9A-Za-z_\-]{35}|sk-[0-9A-Za-z_\-]{20,}|ghp_[0-9A-Za-z]{36,}"
        r"|://[^:/@\s]+:[^@\s]+@)"
    )
    for name in (
        "railway.toml",
        "vercel.json",
        "Dockerfile.dashboard",
        "requirements-dashboard.txt",
        "api/requirements.txt",
        ".env.example",
    ):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not suspicious.search(text), f"{name} looks like it contains a credential"


def test_the_dashboard_image_bakes_in_no_provider_authorisation():
    """A key in a layer is a key in the registry, and LIVE must stay deliberate."""
    dockerfile = (ROOT / "Dockerfile.dashboard").read_text(encoding="utf-8")
    env_lines = [
        line for line in dockerfile.splitlines()
        if line.strip().startswith("ENV") or re.match(r"^\s{4}[A-Z_]+=", line)
    ]
    baked = "\n".join(env_lines)
    assert "GEMINI_API_KEY" not in baked
    assert "AGENT_PLATFORM_LIVE" not in baked


# ================================ the build context, which is not the repository


def _copy_sources(dockerfile: Path) -> list[str]:
    """Every path a COPY reads, with flags and the destination stripped."""
    sources: list[str] = []
    for line in _instructions(dockerfile).splitlines():
        if not line.startswith("COPY "):
            continue
        parts = [p for p in line[len("COPY ") :].split() if not p.startswith("--")]
        sources.extend(parts[:-1])  # the last argument is the destination
    return sources


def _ignore_allows(ignore: Path, candidate: str) -> bool:
    """Whether a deny-by-default ignore file lets ``candidate`` through.

    Only the shapes these files actually use: a bare ``*`` that excludes
    everything, and ``!prefix`` lines that add paths back. Enough to answer the
    question that matters -- did an allow-list forget one of the COPY sources --
    without reimplementing Docker's matcher.
    """
    allowed = [
        line[1:].rstrip("/")
        for line in ignore.read_text(encoding="utf-8").splitlines()
        if line.startswith("!")
    ]
    target = candidate.rstrip("/")
    return any(target == rule or target.startswith(f"{rule}/") for rule in allowed)


def test_the_dashboard_build_context_contains_everything_the_image_copies():
    """The failure this prevents is a build, not a test.

    The root `.dockerignore` is deny-by-default and written for the API image.
    It never allows `dashboard/` or `requirements-dashboard.txt`, and it excludes
    `.streamlit/` by name -- correctly, since the API renders no pages. A
    dashboard build using that context would fail on three separate COPY
    instructions, and nothing in this suite would have noticed.
    """
    ignore = ROOT / "Dockerfile.dashboard.dockerignore"
    assert ignore.is_file(), (
        "without a Dockerfile-specific ignore file the build falls back to the "
        "API's .dockerignore, which excludes the dashboard and its config"
    )

    missing = [
        source
        for source in _copy_sources(ROOT / "Dockerfile.dashboard")
        if not _ignore_allows(ignore, source)
    ]
    assert not missing, f"COPY sources excluded from the build context: {missing}"


def test_the_dashboard_context_still_refuses_credentials():
    """A wider allow-list must not become a wider door."""
    ignore = (ROOT / "Dockerfile.dashboard.dockerignore").read_text(encoding="utf-8")
    assert ignore.lstrip().startswith("#") or "*" in ignore.splitlines()
    assert "\n*\n" in ignore, "the context must stay deny-by-default"
    for secret in (".env", "*.pem", "*.key", ".git/", "secrets/"):
        assert f"\n{secret}\n" in ignore, f"{secret} is no longer excluded"
    assert "!.env" not in ignore


def test_the_api_build_context_is_unchanged_by_the_dashboard():
    """The production context stays exactly as strict as it was."""
    root_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!dashboard/" not in root_ignore, "the API context gained the dashboard"
    assert "!.streamlit/" not in root_ignore
    assert "!requirements-dashboard.txt" not in root_ignore


# ===================================== the build fails when the install does


def test_railway_names_the_dashboard_dockerfile_in_the_repository(railway):
    """Not only in a service variable: a new service must not build the API."""
    assert railway["build"]["dockerfilePath"] == "Dockerfile.dashboard"
    assert (ROOT / railway["build"]["dockerfilePath"]).is_file()


@pytest.mark.parametrize("name", ["Dockerfile", "Dockerfile.dashboard"])
def test_no_install_step_is_followed_by_an_unconditional_true(name):
    """`a && pip install ... || true && b` turned a failed install into success.

    In a `&&` chain an `|| true` excuses everything to its left, so the image
    built without its dependencies and failed only when it started. Nothing in
    a RUN that installs may be excused.
    """
    runs = re.split(r"\n(?=[A-Z]+ )", _instructions(ROOT / name))
    for run in runs:
        if run.startswith("RUN") and "pip install" in run:
            assert "|| true" not in run, run
            assert "|| :" not in run, run


# ============================================================ R6: Vercel upload


@pytest.fixture(scope="module")
def vercel_ignored(tmp_path_factory):
    """Whether `.vercelignore` excludes a path, by git's own matcher.

    `.vercelignore` uses gitignore syntax, so git answers it -- negations and
    all -- in an empty repository whose only rule file is a copy of it. Asked in
    this repository instead, the answer would mix in `.gitignore`, which Vercel
    never reads.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is not available")
    scratch = tmp_path_factory.mktemp("vercel")
    git = shutil.which("git")
    subprocess.run([git, "init", "-q"], cwd=scratch, check=True)  # noqa: S603 - fixed argv
    (scratch / ".gitignore").write_text(
        (ROOT / ".vercelignore").read_text(encoding="utf-8"), encoding="utf-8"
    )

    def ignored(path: str) -> bool:
        result = subprocess.run(  # noqa: S603 - fixed argv, paths are test literals
            [git, "check-ignore", "--no-index", "-q", path],
            cwd=scratch, capture_output=True, check=False,
        )
        assert result.returncode in (0, 1), result.stderr
        return result.returncode == 0

    return ignored


@pytest.mark.parametrize(
    "path", [".env", ".env.local", "dashboard/.env", "tests/conftest.py",
             "docs/audit-report.md", "node_modules/x.js", "dashboard/app.py"],
)
def test_vercel_never_uploads_secrets_or_unrelated_trees(vercel_ignored, path):
    assert vercel_ignored(path), path


@pytest.mark.parametrize(
    "path", ["api/index.py", "api/requirements.txt", "requirements-api.txt",
             "src/agent_platform/api/app.py", "data/kb_vectors.db"],
)
def test_vercel_uploads_what_the_function_imports(vercel_ignored, path):
    assert not vercel_ignored(path), path
