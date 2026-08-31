"""Where the vector index is found, across the layouts the package actually runs in.

The bug this file exists for: ``DEFAULT_INDEX_PATH`` was computed as
``Path(__file__).resolve().parents[3] / "data" / "kb_vectors.db"``. Those four
levels walk ``retrieval -> agent_platform -> src -> <repo>``, which is right in a
source checkout and wrong everywhere else. Installed into ``site-packages`` the
same expression climbs *out* of the package directory entirely, so the index was
looked for in a directory that has no reason to contain it.

It survived because the whole offline suite runs from a checkout, where the
expression happens to be correct. Nothing exercised an installed layout, so
nothing noticed that hybrid retrieval could not find its index anywhere else.

These tests exercise the layouts directly, including a real wheel installed into
a real target directory.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_platform.retrieval import strategy
from agent_platform.tools.dataset import dataset_digest

DIGEST = "db512de8207f751e"
REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_vectors_7e.json"

INDEX_AVAILABLE = (REPO / "data" / "kb_vectors.db").exists()
needs_index = pytest.mark.skipif(
    not INDEX_AVAILABLE, reason="the persisted vector index is not present"
)


# ============================================================ the checkout


@needs_index
def test_the_checkout_layout_still_resolves_to_the_real_index():
    """Scenario 1. The behaviour that already worked must keep working."""
    resolved = strategy.resolve_index_path()
    assert resolved.exists(), f"{resolved} does not exist"
    assert resolved.name == "kb_vectors.db"
    assert resolved == (REPO / "data" / "kb_vectors.db")


@needs_index
def test_the_module_level_default_points_at_the_real_index():
    assert strategy.DEFAULT_INDEX_PATH.exists()


# ==================================================== independence from cwd


@needs_index
@pytest.mark.parametrize("where", ["tmp", "repo_parent", "package_dir"])
def test_resolution_does_not_depend_on_the_working_directory(tmp_path, monkeypatch, where):
    """Scenario 5.

    A retrieval index that moves when the process is started from a different
    folder produces results that change for no stated reason. Resolution is
    anchored to the module's own location, never to ``cwd``.
    """
    destinations = {
        "tmp": tmp_path,
        "repo_parent": REPO.parent,
        "package_dir": Path(strategy.__file__).resolve().parent,
    }
    monkeypatch.chdir(destinations[where])
    assert strategy.resolve_index_path() == (REPO / "data" / "kb_vectors.db")


def test_no_candidate_is_relative(monkeypatch):
    """A relative candidate would silently become cwd-dependent."""
    monkeypatch.delenv(strategy.INDEX_PATH_ENV, raising=False)
    for candidate in (strategy._package_index_path(), strategy._checkout_index_path()):
        assert candidate.is_absolute(), f"{candidate} is relative"


# ============================================== explicit configuration wins


def test_the_environment_variable_overrides_everything(tmp_path, monkeypatch):
    """Scenario 2's mechanism: a deployment states where the index is."""
    elsewhere = tmp_path / "mounted" / "kb_vectors.db"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(b"not a real index, but a real path")

    monkeypatch.setenv(strategy.INDEX_PATH_ENV, str(elsewhere))
    assert strategy.resolve_index_path() == elsewhere


def test_the_environment_variable_wins_even_when_it_does_not_exist(tmp_path, monkeypatch):
    """Explicit configuration must not be silently second-guessed.

    If an operator names a path and it is wrong, the failure has to name *their*
    path. Falling back to a guess would hide the misconfiguration behind an
    index they did not choose.
    """
    missing = tmp_path / "absent" / "kb_vectors.db"
    monkeypatch.setenv(strategy.INDEX_PATH_ENV, str(missing))
    assert strategy.resolve_index_path() == missing


def test_a_blank_environment_variable_is_ignored(monkeypatch):
    monkeypatch.setenv(strategy.INDEX_PATH_ENV, "   ")
    assert strategy.resolve_index_path() == strategy._checkout_index_path()


# ================================================= the installed wheel layout


@pytest.fixture(scope="module")
def installed_package(tmp_path_factory) -> Path:
    """Build the real wheel and install it into a site-packages-like directory.

    Not a simulation of an installed layout -- an actual one, built by the
    project's own build backend, because the bug was invisible to every test
    that ran from the checkout.
    """
    workdir = tmp_path_factory.mktemp("wheelinstall")
    wheelhouse = workdir / "wheel"
    target = workdir / "sitepkg"

    # S603: every argument is this interpreter or a path this test just built.
    # Nothing here comes from outside the test.
    build = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "wheel", str(REPO), "--no-deps", "-w", str(wheelhouse)],
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"could not build the wheel: {build.stderr[-400:]}")

    wheels = list(wheelhouse.glob("agent_platform-*.whl"))
    assert wheels, "the build produced no wheel"

    install = subprocess.run(  # noqa: S603 - interpreter plus locally built paths
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target),
         str(wheels[0])],
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        pytest.skip(f"could not install the wheel: {install.stderr[-400:]}")

    assert (target / "agent_platform" / "retrieval" / "strategy.py").exists()
    return target


def _resolve_in(target: Path, env: dict[str, str] | None = None) -> dict[str, object]:
    """Ask the *installed* copy where it thinks the index is."""
    import os as _os

    script = (
        "import json, os\n"
        "from agent_platform.retrieval import strategy\n"
        "p = strategy.resolve_index_path()\n"
        "print(json.dumps({'path': str(p), 'exists': p.exists(), "
        "'package_dir': str(__import__('pathlib').Path(strategy.__file__).resolve().parent)}))\n"
    )
    environ = dict(_os.environ)
    environ["PYTHONPATH"] = str(target)
    environ.pop(strategy.INDEX_PATH_ENV, None)
    environ.update(env or {})

    proc = subprocess.run(  # noqa: S603 - interpreter plus a literal script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(target),
        env=environ,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_wheel_ships_the_files_the_package_loads_at_runtime(installed_package):
    """Guard on packaging: a runtime file left out of the wheel breaks only
    once installed, which is the failure mode this whole file is about."""
    for relative in ("persistence/schema.sql", "evaluation/datasets/normal.json"):
        assert (installed_package / "agent_platform" / relative).exists(), (
            f"{relative} is missing from the installed package"
        )


def test_with_no_index_anywhere_the_error_names_the_familiar_path(installed_package):
    """The fallback is a choice, not an accident: a missing index must fail
    naming ``data/kb_vectors.db`` rather than a path nobody has seen.

    This one passes with or without the fix, deliberately -- it guards the
    behaviour the fix had to *preserve*, not the behaviour it added.
    """
    result = _resolve_in(installed_package)
    assert result["exists"] is False
    assert Path(str(result["path"])).name == "kb_vectors.db"
    assert Path(str(result["path"])).parent.name == "data"


@needs_index
def test_an_installed_package_finds_an_index_shipped_beside_it(installed_package):
    """Scenario 2 and 3, without any environment variable.

    A deployment that ships the index as package data must find it with no
    configuration at all.
    """
    shipped = installed_package / "agent_platform" / "retrieval" / "kb_vectors.db"
    shipped.write_bytes((REPO / "data" / "kb_vectors.db").read_bytes())
    try:
        result = _resolve_in(installed_package)
        assert result["exists"] is True, f"installed layout could not find {result['path']}"
        assert Path(str(result["path"])) == shipped
    finally:
        shipped.unlink()


@needs_index
def test_an_installed_package_finds_a_mounted_index(installed_package, tmp_path):
    """Scenario 2 and 3 via explicit configuration -- what a container does."""
    mounted = tmp_path / "mnt" / "kb_vectors.db"
    mounted.parent.mkdir(parents=True)
    mounted.write_bytes((REPO / "data" / "kb_vectors.db").read_bytes())

    result = _resolve_in(installed_package, {strategy.INDEX_PATH_ENV: str(mounted)})
    assert result["exists"] is True
    assert Path(str(result["path"])) == mounted


# ================================== the index is usable, not merely locatable


@needs_index
def test_hybrid_retrieval_opens_the_resolved_index_without_calling_a_provider(monkeypatch):
    """Scenario 3.

    Resolving to a path is not the same as opening a valid index, so this loads
    it for real and retrieves through the hybrid path. Embeddings are replayed
    from the 7E capture -- no provider call is made.
    """
    from tests.integration.test_retrieval_strategy import ReplayProvider

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["model"] == "gemini-embedding-001"
    vectors = payload["query_vectors"]
    question = next(iter(vectors))

    provider = ReplayProvider(vectors)
    strategy.reset_caches()
    try:
        with strategy.retrieval_provider(provider):
            results = strategy.retrieve(question)
    finally:
        strategy.reset_caches()

    assert provider.embed_calls == 1
    assert results, "hybrid retrieval returned nothing from the resolved index"
    assert all(r.doc_id.startswith("KB-") for r in results)


def test_the_stub_path_never_touches_the_index(monkeypatch):
    """Scenario 4. Lexical retrieval must not depend on the index at all, so a
    deployment with no index still serves demo traffic."""
    monkeypatch.setattr(strategy, "DEFAULT_INDEX_PATH", Path("does-not-exist.db"))
    strategy.reset_caches()
    try:
        results = strategy.retrieve("How do I request a refund?")
    finally:
        strategy.reset_caches()
    assert results, "lexical retrieval returned nothing"


# ================================================================ invariants


def test_the_threshold_is_untouched():
    assert strategy.MIN_COSINE_SIMILARITY == 0.67


def test_the_dataset_is_unchanged():
    assert dataset_digest() == DIGEST


@needs_index
def test_the_index_is_opened_read_only_and_left_intact():
    """This change is about locating the index, never about rewriting it.

    Resolution and loading must not mutate the file, so the size and mtime are
    compared across a full hybrid load.
    """
    index = REPO / "data" / "kb_vectors.db"
    before = index.stat()

    strategy.reset_caches()
    try:
        strategy.resolve_index_path()
        strategy._load_index()
    finally:
        strategy.reset_caches()

    after = index.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime == before.st_mtime
