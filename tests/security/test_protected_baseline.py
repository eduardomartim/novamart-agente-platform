"""The protected-files baseline, and whether checking it means anything.

Twelve files carry a pinned SHA-256 in ``PROTECTED.sha256``. They are the ones
where a change is either a mistake or a decision somebody has to make
deliberately: the policy engine and its rules, the gateway's execution path, the
simulated dataset, the graph, the execution grant, the MCP server, the
repository contract and its SQLite implementation, the shared-state backend, the
production Dockerfile, and the vector index.

A manifest that only ever agrees with itself proves nothing, so these tests do
two separate things:

* verify the manifest against the **real files on disk**, so a genuine change
  breaks the suite;
* verify the **checker** against a deliberately corrupted copy in a temporary
  directory, so an ineffective checker breaks the suite too.

The repository is never modified. Every tampering happens on a copy.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "PROTECTED.sha256"
CHECKER = REPO / "scripts" / "check_protected.py"

BINARY_SUFFIXES = (".db",)


def entries() -> list[tuple[str, str]]:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            expected, _, name = line.partition("  ")
            rows.append((expected, name.strip()))
    return rows


def canonical_digest(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix not in BINARY_SUFFIXES:
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


# ============================================================== the manifest


def test_the_manifest_exists_and_lists_twelve_files():
    assert MANIFEST.is_file()
    assert len(entries()) == 12


def test_every_listed_file_exists():
    missing = [name for _, name in entries() if not (REPO / name).is_file()]
    assert missing == [], f"the manifest names files that are not here: {missing}"


@pytest.mark.parametrize(("expected", "name"), entries(), ids=[n for _, n in entries()])
def test_each_protected_file_matches_the_baseline(expected: str, name: str):
    """Against the real file. This is the test a real change has to break."""
    actual = canonical_digest(REPO / name)
    assert actual == expected, (
        f"{name} no longer matches the baseline.\n"
        f"  expected {expected}\n  actual   {actual}\n"
        "If the change was deliberate, the baseline is a decision to record, "
        "not a file to regenerate."
    )


def test_the_manifest_matches_what_git_stores():
    """The canonical form is the blob, so the two must not drift apart.

    A working tree can hold CRLF where Git holds LF; the manifest describes the
    LF content. If someone re-pins the manifest from a CRLF working tree, this
    is what catches it.
    """
    for expected, name in entries():
        blob = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "show", f"HEAD:{name}"],
            cwd=REPO, capture_output=True, timeout=60,
        )
        if blob.returncode != 0:
            pytest.skip("not a git checkout, or the file is not committed yet")
        assert hashlib.sha256(blob.stdout).hexdigest() == expected, (
            f"{name}: the manifest does not describe what git stores"
        )


def test_the_manifest_is_sha256sum_format():
    """CI runs `sha256sum -c` on it directly, so the format is load-bearing."""
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, name = line.partition("  ")
        assert separator == "  ", f"expected two spaces: {line[:40]!r}"
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        assert name.strip() and not name.startswith(" ")


# =========================================================== the checker works


def run_checker(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, str(cwd / "scripts" / "check_protected.py")],
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A copy of just enough repository for the checker to run."""
    shutil.copy2(MANIFEST, tmp_path / "PROTECTED.sha256")
    (tmp_path / "scripts").mkdir()
    shutil.copy2(CHECKER, tmp_path / "scripts" / "check_protected.py")
    for _, name in entries():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    return tmp_path


def test_the_checker_passes_on_an_intact_copy(sandbox: Path):
    result = run_checker(sandbox)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "12/12" in result.stdout


def test_the_checker_fails_when_a_protected_file_changes(sandbox: Path):
    """Non-vacuity. A checker that cannot fail is not a check.

    The tampering is a comment -- the smallest edit that changes nothing about
    behaviour. If the gate only noticed large changes it would miss exactly the
    edits worth catching.
    """
    victim = sandbox / "src/agent_platform/guardrails/policy.py"
    victim.write_bytes(victim.read_bytes() + b"\n# an unauthorised edit\n")

    result = run_checker(sandbox)
    assert result.returncode == 1
    assert "policy.py: FAILED" in result.stdout
    assert "1 of 12" in result.stdout


def test_the_checker_notices_a_single_byte(sandbox: Path):
    """One character, in the middle, in the binary entry."""
    victim = sandbox / "data/kb_vectors.db"
    raw = bytearray(victim.read_bytes())
    midpoint = len(raw) // 2
    raw[midpoint] ^= 0x01
    victim.write_bytes(bytes(raw))

    result = run_checker(sandbox)
    assert result.returncode == 1
    assert "kb_vectors.db: FAILED" in result.stdout


def test_the_checker_fails_when_a_protected_file_is_deleted(sandbox: Path):
    (sandbox / "Dockerfile").unlink()
    result = run_checker(sandbox)
    assert result.returncode == 1
    assert "Dockerfile: MISSING" in result.stdout


def test_the_checker_refuses_an_empty_manifest(sandbox: Path):
    """An emptied manifest must not read as twelve silent passes."""
    (sandbox / "PROTECTED.sha256").write_text("# nothing here\n", encoding="utf-8")
    result = run_checker(sandbox)
    assert result.returncode == 1
    assert "refusing to report success" in result.stdout


def test_line_endings_alone_do_not_trip_the_checker(sandbox: Path):
    """The false alarm this checker exists to avoid.

    A Windows working tree holds CRLF where Git holds LF. That is not a change
    to the file, and a gate that reported it would be switched off by the first
    developer it inconvenienced -- taking the real protection with it.
    """
    victim = sandbox / "src/agent_platform/guardrails/rules.py"
    victim.write_bytes(victim.read_bytes().replace(b"\n", b"\r\n"))

    result = run_checker(sandbox)
    assert result.returncode == 0, result.stdout


def test_the_binary_entry_is_never_normalised(sandbox: Path):
    """And the complement: normalisation must not reach the index.

    Applying CRLF translation to the vector index would corrupt it, so the
    checker treats it as bytes. Rewriting its LF bytes as CRLF is a real
    corruption and must be caught.
    """
    victim = sandbox / "data/kb_vectors.db"
    original = victim.read_bytes()
    tampered = original.replace(b"\n", b"\r\n")
    if tampered == original:
        pytest.skip("the index happens to contain no LF bytes to rewrite")

    victim.write_bytes(tampered)
    result = run_checker(sandbox)
    assert result.returncode == 1
    assert "kb_vectors.db: FAILED" in result.stdout
