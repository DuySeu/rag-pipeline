from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.corpus import load_documents
from ingestion.loaders import Section
from rag_core.openrouter import Embedder
from rag_core.settings import Settings
from rag_core.store import recreate_collection

CHROMA_ADD_BATCH_SIZE = 1000
# Bump whenever chunking or loader logic changes, so stores built with different logic are told apart
CHUNK_STRATEGY = "section-window-v1"


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    index: int
    text: str
    location: dict[str, str | int] = field(default_factory=dict)


# Split text into windows of at most size characters that overlap by overlap characters
def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    # ponytail: fixed character window, switch to a sentence/heading splitter if recall suffers
    chunks: list[str] = []
    start = 0
    while start < len(text):
        piece = text[start : start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


# Pack whole lines into chunks of at most size characters, windowing only a line longer than that
def chunk_lines(text: str, size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        if len(line) > size:
            chunks.extend(filter(None, [current]))
            chunks.extend(chunk_text(line, size, overlap))
            current = ""
        elif current and len(current) + 1 + len(line) > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    return chunks + [current] if current.strip() else chunks


# Turn loaded documents into chunks with stable ids; indexes run on across a file's sections
def chunk_documents(
    documents: Mapping[str, Sequence[Section]], size: int, overlap: int
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for source, sections in documents.items():
        index = 0
        for section in sections:
            split = chunk_lines if section.line_wise else chunk_text
            for text in split(section.text, size, overlap):
                chunks.append(Chunk(f"{source}#{index}", source, index, text, section.location))
                index += 1
    return chunks


# Turn every document under docs_dir into chunks with stable ids
def build_chunks(docs_dir: Path, size: int, overlap: int) -> list[Chunk]:
    return chunk_documents(load_documents(docs_dir), size, overlap)


# The settings a store was built with, written to the collection so mismatches can be detected
def collection_metadata(settings: Settings, corpus_hash: str) -> dict[str, str | int]:
    return {
        "embed_model": settings.embed_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "chunk_strategy": CHUNK_STRATEGY,
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
