from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rag_core.openrouter import Embedder
from rag_core.settings import Settings
from rag_core.store import recreate_collection

SUPPORTED_SUFFIXES = {".md", ".txt"}
CHROMA_ADD_BATCH_SIZE = 1000


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    index: int
    text: str


# Read every supported file under docs_dir, keyed by its path relative to docs_dir
def load_documents(docs_dir: Path) -> dict[str, str]:
    paths = sorted(p for p in docs_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)
    return {p.relative_to(docs_dir).as_posix(): p.read_text(encoding="utf-8") for p in paths}


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


# Turn every document under docs_dir into chunks with stable ids
def build_chunks(docs_dir: Path, size: int, overlap: int) -> list[Chunk]:
    return [
        Chunk(id=f"{source}#{index}", source=source, index=index, text=text)
        for source, content in load_documents(docs_dir).items()
        for index, text in enumerate(chunk_text(content, size, overlap))
    ]


# Rebuild the vector store from chunks and return how many were stored
def ingest(chunks: Sequence[Chunk], settings: Settings, embed: Embedder) -> int:
    if not chunks:
        raise ValueError("No chunks to ingest; the docs folder has no .md or .txt content")

    # Embed first so a failed API call leaves the previous index untouched
    # ponytail: full rebuild re-embeds every doc, add per-file hashing if the corpus grows
    vectors = embed([c.text for c in chunks])
    collection = recreate_collection(settings)

    for start in range(0, len(chunks), CHROMA_ADD_BATCH_SIZE):
        batch = chunks[start : start + CHROMA_ADD_BATCH_SIZE]
        collection.add(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[{"source": c.source, "index": c.index} for c in batch],
            embeddings=vectors[start : start + CHROMA_ADD_BATCH_SIZE],
        )

    return collection.count()
