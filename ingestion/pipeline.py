from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from core import Embedder, Settings, recreate_collection
from ingestion.chunking import CHUNKING_VERSION, STRATEGIES, chunk_lines
from ingestion.corpus import load_documents
from ingestion.loaders import Section

CHROMA_ADD_BATCH_SIZE = 1000


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    index: int
    text: str
    location: dict[str, str | int] = field(default_factory=dict)


# Turn loaded documents into chunks with stable ids; indexes run on across a file's sections
def chunk_documents(
    documents: Mapping[str, Sequence[Section]], size: int, overlap: int, strategy: str = "fixed"
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
                chunks.append(Chunk(f"{source}#{index}", source, index, text, section.location))
                index += 1
    return chunks


# Turn every document under docs_dir into chunks with stable ids
def build_chunks(docs_dir: Path, size: int, overlap: int, strategy: str = "fixed") -> list[Chunk]:
    return chunk_documents(load_documents(docs_dir), size, overlap, strategy)


# The settings a store was built with, written to the collection so mismatches can be detected
def collection_metadata(settings: Settings, corpus_hash: str) -> dict[str, str | int]:
    return {
        "embed_model": settings.embed_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "chunk_strategy": f"{settings.chunk_strategy}-v{CHUNKING_VERSION}",
        "embed_query_prefix": settings.embed_query_prefix,
        "embed_passage_prefix": settings.embed_passage_prefix,
        "corpus_hash": corpus_hash,
    }


# Rebuild the vector store from chunks and return how many were stored
def ingest(chunks: Sequence[Chunk], settings: Settings, embed: Embedder, corpus_hash: str) -> int:
    if not chunks:
        raise ValueError("No chunks to ingest; the docs folder has no supported content")

    # Embed first so a failed API call leaves the previous index untouched
    # ponytail: full rebuild re-embeds every doc, add per-file hashing if the corpus grows
    vectors = embed([c.text for c in chunks])
    collection = recreate_collection(settings, collection_metadata(settings, corpus_hash))

    for start in range(0, len(chunks), CHROMA_ADD_BATCH_SIZE):
        batch = chunks[start : start + CHROMA_ADD_BATCH_SIZE]
        collection.add(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[{"source": c.source, "index": c.index, **c.location} for c in batch],
            embeddings=vectors[start : start + CHROMA_ADD_BATCH_SIZE],
        )

    return collection.count()
