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
    )

    # Reject a window that would never advance
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be >= 0 and smaller than CHUNK_SIZE")

    return settings
