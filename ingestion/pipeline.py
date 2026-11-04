from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from chromadb.api.models.Collection import Collection

from core.config import Settings
from core.openrouter import Embedder
from core.store import find_collection, recreate_collection
from ingestion.chunking import CHUNKING_VERSION, STRATEGIES, chunk_lines
from ingestion.corpus import corpus_hash_from, file_hashes, load_documents
from ingestion.loaders import Section

CHROMA_ADD_BATCH_SIZE = 1000
# Nemotron Embed (EMBED_MODEL_ID in main.py) was trained with "query: " before questions and
# "passage: " before documents; without them retrieval degrades silently. Models trained without
# prefixes (e.g. Cohere) take none: drop this one and QUERY_PREFIX in retrieval/pipeline.py, then re-ingest
PASSAGE_PREFIX = "passage: "
NO_CHUNKS_MESSAGE = "No chunks to ingest; the docs folder has no supported content"


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    index: int
    text: str
    location: dict[str, str | int] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangePlan:
    new: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged: tuple[str, ...]


@dataclass(frozen=True)
class IngestResult:
    plan: ChangePlan | None  # None when the store was rebuilt from scratch
    embedded_chunks: int
    stored_chunks: int
    empty_files: tuple[str, ...]


# Turn loaded documents into chunks with stable ids; indexes run on across a file's sections
def chunk_documents(
    documents: Mapping[str, Sequence[Section]],
    size: int,
    overlap: int,
    strategy: str = "fixed",
) -> list[Chunk]:
    if strategy not in STRATEGIES:
        raise ValueError(
            f"Unknown chunk_strategy {strategy!r}; use one of: {' | '.join(STRATEGIES)}"
        )

    chunks: list[Chunk] = []
    for source, sections in documents.items():
        index = 0
        for section in sections:
            # Line-wise sections (Excel rows) are always packed whole, whatever the strategy
            split = chunk_lines if section.line_wise else STRATEGIES[strategy]
            for text in split(section.text, size, overlap):
                chunks.append(
                    Chunk(f"{source}#{index}", source, index, text, section.location)
                )
                index += 1
    return chunks


# Turn every document under docs_dir into chunks with stable ids
def build_chunks(
    docs_dir: Path, size: int, overlap: int, strategy: str = "fixed"
) -> list[Chunk]:
    return chunk_documents(load_documents(docs_dir), size, overlap, strategy)


# Sort files into new, changed, removed and unchanged by comparing stored and current hashes
def plan_changes(stored: Mapping[str, str], current: Mapping[str, str]) -> ChangePlan:
    both = current.keys() & stored.keys()
    return ChangePlan(
        new=tuple(sorted(current.keys() - stored.keys())),
        changed=tuple(sorted(s for s in both if current[s] != stored[s])),
        removed=tuple(sorted(stored.keys() - current.keys())),
        unchanged=tuple(sorted(s for s in both if current[s] == stored[s])),
    )


# The settings a store was built with, written to the collection so mismatches can be detected
def collection_metadata(settings: Settings, corpus_hash: str) -> dict[str, str | int]:
    return {
        "embed_model": settings.embed_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "chunk_strategy": f"{settings.chunk_strategy}-v{CHUNKING_VERSION}",
        "embed_passage_prefix": PASSAGE_PREFIX,
        "corpus_hash": corpus_hash,
    }


# Bring the store in line with docs_dir: embed only new and changed files, or rebuild it all when its settings differ
def ingest(docs_dir: Path, settings: Settings, embed: Embedder) -> IngestResult:
    current = file_hashes(docs_dir)
    if not current:
        raise ValueError(NO_CHUNKS_MESSAGE)
    metadata = collection_metadata(settings, corpus_hash_from(current))

    # Rebuild when there is no usable store: missing, empty, other settings, an interrupted
    # incremental run (corpus_hash ""), or chunks written before per-file hashing
    collection = find_collection(settings)
    stored_metadatas = (collection.get(include=["metadatas"])["metadatas"] or []) if collection else []
    stored_config = (collection.metadata or {}) if collection else {}
    if (
        not stored_metadatas
        or stored_config.get("corpus_hash") == ""
        or any(k != "corpus_hash" and stored_config.get(k) != v for k, v in metadata.items())
        or any("file_hash" not in m for m in stored_metadatas)
    ):
        documents = load_documents(docs_dir)
        chunks = chunk_documents(
            documents, settings.chunk_size, settings.chunk_overlap, settings.chunk_strategy
        )
        if not chunks:
            raise ValueError(NO_CHUNKS_MESSAGE)

        # Embed first so a failed API call leaves the previous index untouched
        vectors = embed([PASSAGE_PREFIX + c.text for c in chunks])
        collection = recreate_collection(settings, metadata)
        _add_chunks(collection, chunks, vectors, current)
        return IngestResult(None, len(chunks), collection.count(), _empty_files(documents, chunks))

    stored = {str(m["source"]): str(m["file_hash"]) for m in stored_metadatas}
    plan = plan_changes(stored, current)
    documents = load_documents(docs_dir, only={*plan.new, *plan.changed})
    chunks = chunk_documents(
        documents, settings.chunk_size, settings.chunk_overlap, settings.chunk_strategy
    )

    # Embed first so a failed API call leaves the previous index untouched
    vectors = embed([PASSAGE_PREFIX + c.text for c in chunks]) if chunks else []
    outdated = [*plan.changed, *plan.removed]
    if chunks or outdated:
        # Mark the store dirty so a run that dies between delete and add is rebuilt next time
        collection.modify(metadata=collection_metadata(settings, ""))
        if outdated:
            collection.delete(where={"source": {"$in": outdated}})
        _add_chunks(collection, chunks, vectors, current)

    # Also covers files without chunks, which change corpus_hash but never reach the plan
    if (collection.metadata or {}).get("corpus_hash") != metadata["corpus_hash"]:
        collection.modify(metadata=metadata)

    return IngestResult(plan, len(chunks), collection.count(), _empty_files(documents, chunks))


# Write chunks with their vectors and file hash in batches Chroma accepts
def _add_chunks(
    collection: Collection,
    chunks: Sequence[Chunk],
    vectors: Sequence[Sequence[float]],
    hashes: Mapping[str, str],
) -> None:
    for start in range(0, len(chunks), CHROMA_ADD_BATCH_SIZE):
        batch = chunks[start : start + CHROMA_ADD_BATCH_SIZE]
        collection.add(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[
                {"source": c.source, "index": c.index, **c.location, "file_hash": hashes[c.source]}
                for c in batch
            ],
            embeddings=vectors[start : start + CHROMA_ADD_BATCH_SIZE],
        )


# Files that loaded but produced no chunk (e.g. image-only PDFs)
def _empty_files(documents: Mapping[str, Sequence[Section]], chunks: Sequence[Chunk]) -> tuple[str, ...]:
    return tuple(sorted(documents.keys() - {c.source for c in chunks}))
