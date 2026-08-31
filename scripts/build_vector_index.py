"""Build the knowledge-base vector index. Offline, explicit, human-invoked.

    python scripts/build_vector_index.py --dry-run    # costs nothing
    python scripts/build_vector_index.py              # spends 12 provider calls

Why this is a script and not a startup path
-------------------------------------------
Embedding the corpus costs one physical provider call per document, charged
against the same daily ceiling as every completion. A runtime that could
rebuild the index on a cache miss is a runtime that can spend the day's quota
because a file was missing. So the build is something a person does on purpose,
and the request path can only ever *load* what this script wrote.

What it refuses to do
---------------------
Overwrite an index that is already current: if the corpus fingerprint and model
match, it exits without spending anything. A rebuild has to be asked for with
``--force``, because "run the build script twice" should not silently cost 24
calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from agent_platform.config import Settings  # noqa: E402
from agent_platform.llm import build_provider  # noqa: E402
from agent_platform.llm.gemini import DEFAULT_EMBEDDING_MODEL  # noqa: E402
from agent_platform.llm.provider import EmbedTask  # noqa: E402
from agent_platform.retrieval.index import (  # noqa: E402
    IndexIntegrityError,
    VectorIndex,
    build_index,
)
from agent_platform.retrieval.model import build_chunks, corpus_fingerprint  # noqa: E402
from agent_platform.tools.dataset import dataset_digest  # noqa: E402

DEFAULT_INDEX_PATH = ROOT / "data" / "kb_vectors.db"


def describe(chunks, fingerprint: str, model: str, path: Path) -> None:
    print("=" * 72)
    print("VECTOR INDEX BUILD")
    print("=" * 72)
    print(f"  dataset digest     : {dataset_digest()}")
    print(f"  corpus fingerprint : {fingerprint}")
    print(f"  documents          : {len(chunks)}")
    print(f"  embedding model    : {model}")
    print(f"  task type          : {EmbedTask.DOCUMENT.value}")
    print(f"  index path         : {path}")
    print(f"  physical calls     : {len(chunks)} (one per document, each charged)")
    print()
    for chunk in chunks:
        print(f"    {chunk.doc_id:<28} {chunk.title}")
    print()


def current_index_is_fresh(path: Path, model: str, fingerprint: str, doc_ids) -> bool:
    try:
        VectorIndex.load(
            path,
            expected_model=model,
            expected_fingerprint=fingerprint,
            expected_doc_ids=doc_ids,
        )
    except IndexIntegrityError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report exactly what would be spent, then exit without calling anything",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild even if the existing index already matches this corpus",
    )
    parser.add_argument("--index-path", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    args = parser.parse_args()

    chunks = build_chunks()
    fingerprint = corpus_fingerprint(chunks)
    doc_ids = tuple(chunk.doc_id for chunk in chunks)

    describe(chunks, fingerprint, args.model, args.index_path)

    if args.dry_run:
        print("DRY RUN -- no provider call was made, no budget was charged.")
        return 0

    if not args.force and current_index_is_fresh(
        args.index_path, args.model, fingerprint, doc_ids
    ):
        print("The existing index already matches this corpus and model.")
        print("Nothing to do. Pass --force to rebuild and spend the calls again.")
        return 0

    settings = Settings.from_env()
    if settings.demo_mode:
        print(
            "ERROR: no API key is configured, so the provider is the deterministic "
            "stub.\nThe stub refuses to embed rather than fabricating vectors, "
            "which is deliberate:\nan index of fake vectors would make offline "
            "retrieval look semantic while ranking\nat random. Configure a real "
            "provider to build the index."
        )
        return 2

    provider = build_provider(settings)
    print(f"Embedding {len(chunks)} documents with {provider.name}...")

    vectors: dict[str, tuple[float, ...]] = {}
    for position, chunk in enumerate(chunks, start=1):
        embedding = provider.embed(chunk.embed_text, task=EmbedTask.DOCUMENT)
        vectors[chunk.doc_id] = embedding.vector
        print(
            f"  [{position:>2}/{len(chunks)}] {chunk.doc_id:<28} "
            f"dim={embedding.dimensions} model={embedding.model}"
        )

    metadata = build_index(
        args.index_path,
        vectors=vectors,
        model_id=args.model,
        corpus_fingerprint=fingerprint,
        expected_doc_ids=doc_ids,
    )

    print()
    print("=" * 72)
    print("BUILT")
    print("=" * 72)
    print(f"  documents   : {metadata.document_count}")
    print(f"  dimension   : {metadata.dimension}")
    print(f"  model       : {metadata.model_id}")
    print(f"  fingerprint : {metadata.corpus_fingerprint}")
    print(f"  built at    : {metadata.built_at}")
    print(f"  file size   : {args.index_path.stat().st_size} bytes")
    print(f"  dataset digest unchanged: {dataset_digest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
