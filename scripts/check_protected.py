#!/usr/bin/env python
"""Verify the protected files against PROTECTED.sha256, on any platform.

On Linux this is not the primary check. CI runs the manifest through its native
tool::

    sha256sum -c PROTECTED.sha256

which is stronger: it compares raw bytes with nothing in between. This script
exists for the machines where that command either does not exist or gives a
false alarm.

The false alarm is worth explaining, because it is the reason this file is not
redundant. The canonical hashes describe the **LF** content -- the bytes Git
stores, and the bytes a checkout produces anywhere ``.gitattributes`` is
honoured. A Windows working tree can hold CRLF for the same files, byte for
byte different and logically identical. `sha256sum -c` would report four
failures on an untouched repository, and a check that cries wolf on a clean tree
is a check people learn to ignore.

So text entries are compared after normalising CRLF to LF, and binary entries
are compared byte for byte with no normalisation at all. Any change that is not
a line ending still fails, which is the property that matters.

Never writes, never restores. It reports; fixing is a person's job.

Exit 0 when every file matches.
"""

from __future__ import annotations

import hashlib
import pathlib

MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "PROTECTED.sha256"
REPO = MANIFEST.parent

#: Suffixes never normalised. Mirrors the `*.db binary` rule in .gitattributes:
#: one byte of line-ending translation would corrupt the vector index.
BINARY_SUFFIXES = (".db",)


def digest(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    if path.suffix not in BINARY_SUFFIXES:
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    if not MANIFEST.is_file():
        print(f"missing manifest: {MANIFEST}")
        return 1

    entries: list[tuple[str, str]] = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        expected, _, name = line.partition("  ")
        if not expected or not name:
            print(f"malformed manifest line: {line[:60]!r}")
            return 1
        entries.append((expected, name.strip()))

    if not entries:
        print("the manifest lists no files; refusing to report success")
        return 1

    failures: list[str] = []
    for expected, name in entries:
        path = REPO / name
        if not path.is_file():
            failures.append(f"{name}: MISSING")
            continue
        actual = digest(path)
        if actual != expected:
            # The path and both digests. Protected files are source, not
            # secrets, so naming what diverged is useful rather than risky.
            failures.append(f"{name}: FAILED\n      expected {expected}\n      actual   {actual}")

    for entry in failures:
        print(f"  {entry}")

    if failures:
        print()
        print(f"{len(failures)} of {len(entries)} protected files do not match the baseline.")
        print("Nothing was modified. If the change was deliberate, the baseline is a")
        print("decision to be made and recorded, not a file to be quietly regenerated.")
        return 1

    print(f"ok: {len(entries)}/{len(entries)} protected files match the baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
