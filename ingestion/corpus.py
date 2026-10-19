from __future__ import annotations

import hashlib
import logging
import unicodedata
from dataclasses import replace
from pathlib import Path

from ingestion.loaders import LOADERS, LoadError, Section

logger = logging.getLogger(__name__)

LOCK_FILE_PREFIX = "~$"


# List the files a loader exists for, sorted by path; Office lock files are not documents
def list_documents(docs_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in LOADERS and not p.name.startswith(LOCK_FILE_PREFIX)
    )


# Read every supported file as NFC-normalized sections, keyed by path relative to docs_dir
def load_documents(docs_dir: Path) -> dict[str, list[Section]]:
    skipped = sorted(
        p.relative_to(docs_dir).as_posix()
        for p in docs_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() not in LOADERS
        and not p.name.startswith(LOCK_FILE_PREFIX)
    )
    if skipped:
        logger.warning("Skipped unsupported files: %s", ", ".join(skipped))

    documents: dict[str, list[Section]] = {}
    for path in list_documents(docs_dir):
        # Any read failure stops the run before embedding, so the old index survives
        try:
            sections = LOADERS[path.suffix.lower()](path)
        except LoadError:
            raise
        except Exception as exc:
            raise LoadError(f"{path}: cannot read file ({exc})") from exc

        documents[path.relative_to(docs_dir).as_posix()] = [
            replace(s, text=unicodedata.normalize("NFC", s.text)) for s in sections
        ]
    return documents


# Fingerprint the corpus from each file's relative path and raw bytes, before any normalization
def corpus_hash(docs_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in list_documents(docs_dir):
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{path.relative_to(docs_dir).as_posix()}\0{file_hash}\n".encode())
    return digest.hexdigest()
