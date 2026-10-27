from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chromadb.api.models.Collection import Collection

from core.config import Settings
from ingestion.corpus import corpus_hash, load_documents
from ingestion.pipeline import chunk_documents, collection_metadata

REPORT_PATH = Path("evaluation/reports/ingestion.json")


# Compare the vector store against a fresh chunking of the docs folder
def evaluate_ingestion(
    docs_dir: Path, settings: Settings, collection: Collection, golden_sources: Sequence[str]
) -> dict[str, Any]:
    documents = load_documents(docs_dir)
    expected = chunk_documents(
        documents, settings.chunk_size, settings.chunk_overlap, settings.chunk_strategy
    )
    expected_ids = {c.id for c in expected}
    expected_sources = {c.source for c in expected}

    stored = collection.get(include=["documents", "metadatas"])
    stored_ids = set(stored["ids"])
    stored_sources = {str(m["source"]) for m in stored["metadatas"] or []}
    texts = stored["documents"] or []
    lengths = [len(t) for t in texts] or [0]

    # Compare the settings and corpus the store was built with against the current ones
    current_hash = corpus_hash(docs_dir)
    stored_config = collection.metadata or {}
    wanted_config = collection_metadata(settings, current_hash)
    config_mismatch = {
        key: {"store": stored_config.get(key), "settings": value}
        for key, value in wanted_config.items()
        if key != "corpus_hash" and stored_config.get(key) != value
    }

    report: dict[str, Any] = {
        "documents": len(expected_sources),
        "expected_chunks": len(expected_ids),
        "stored_chunks": len(stored_ids),
        "documents_not_in_store": sorted(expected_sources - stored_sources),
        "missing_chunks": sorted(expected_ids - stored_ids),
        "stale_chunks": sorted(stored_ids - expected_ids),
        "duplicate_chunks": len(texts) - len(set(texts)),
        "chunk_chars": {
            "min": min(lengths),
            "mean": round(statistics.mean(lengths), 1),
            "max": max(lengths),
        },
        "golden_sources_not_in_store": sorted(set(golden_sources) - stored_sources),
        "corpus_hash": current_hash,
        "corpus_hash_mismatch": stored_config.get("corpus_hash") != current_hash,
        "config_mismatch": config_mismatch,
        "chunks_by_format": dict(Counter(Path(c.source).suffix.lower() for c in expected)),
        "pdf_pages_without_text": [
            f"{source}#page{s.location['page']}"
            for source, sections in documents.items()
            for s in sections
            if "page" in s.location and not s.text.strip()
        ],
    }

    # The store is healthy when it matches the docs folder, was built with the current settings and covers every golden source
    report["passed"] = not (
        report["documents_not_in_store"]
        or report["missing_chunks"]
        or report["stale_chunks"]
        or report["golden_sources_not_in_store"]
        or report["corpus_hash_mismatch"]
        or report["config_mismatch"]
    )
    return report
