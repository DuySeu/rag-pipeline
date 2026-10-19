from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_key: str
    embed_model: str
    chat_model: str
    chroma_dir: Path
    collection: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    embed_query_prefix: str
    embed_passage_prefix: str
    max_distance: float | None


# Read every setting from the environment once at startup
def load_settings() -> Settings:
    settings = Settings(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        embed_model=os.environ.get("EMBED_MODEL", "nvidia/nemotron-3-embed-1b:free"),
        chat_model=os.environ.get("CHAT_MODEL", "google/gemma-4-31b-it:free"),
        chroma_dir=Path(os.environ.get("CHROMA_DIR", "data/chroma")),
        collection=os.environ.get("COLLECTION", "docs"),
        chunk_size=int(os.environ.get("CHUNK_SIZE", "800")),
        chunk_overlap=int(os.environ.get("CHUNK_OVERLAP", "100")),
        top_k=int(os.environ.get("TOP_K", "4")),
        embed_query_prefix=os.environ.get("EMBED_QUERY_PREFIX", "query: "),
        embed_passage_prefix=os.environ.get("EMBED_PASSAGE_PREFIX", "passage: "),
        max_distance=float(os.environ["MAX_DISTANCE"]) if os.environ.get("MAX_DISTANCE") else None,
    )

    # Reject a window that would never advance
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be >= 0 and smaller than CHUNK_SIZE")

    return settings
