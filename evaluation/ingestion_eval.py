from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from chromadb.api.models.Collection import Collection

from evaluation.golden import load_golden
from ingestion.pipeline import build_chunks
from rag_core.settings import Settings, load_settings
from rag_core.store import open_collection

REPORT_PATH = Path("evaluation/reports/ingestion.json")


# Compare the vector store against a fresh chunking of the docs folder
def evaluate_ingestion(
    docs_dir: Path, settings: Settings, collection: Collection, golden_sources: Sequence[str]
) -> dict[str, Any]:
    expected = build_chunks(docs_dir, settings.chunk_size, settings.chunk_overlap)
    expected_ids = {c.id for c in expected}
    expected_sources = {c.source for c in expected}

    stored = collection.get(include=["documents", "metadatas"])
    stored_ids = set(stored["ids"])
    stored_sources = {str(m["source"]) for m in stored["metadatas"] or []}
    texts = stored["documents"] or []
    lengths = [len(t) for t in texts] or [0]

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
    }

    # The store is healthy when it matches the docs folder and covers every golden source
    report["passed"] = not (
        report["documents_not_in_store"]
        or report["missing_chunks"]
        or report["stale_chunks"]
        or report["golden_sources_not_in_store"]
    )
    return report


# Run the ingestion check, write the report and exit non-zero on failure
def main() -> None:
    parser = argparse.ArgumentParser(description="Check the vector store against the docs folder")
    parser.add_argument("--docs", type=Path, default=Path("data/docs"))
    args = parser.parse_args()

    settings = load_settings()
    golden_sources = [s for sample in load_golden() for s in sample.sources]
    report = evaluate_ingestion(args.docs, settings, open_collection(settings), golden_sources)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
