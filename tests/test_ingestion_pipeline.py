from __future__ import annotations

import pytest

from evaluation.ingestion_eval import evaluate_ingestion
from ingestion.pipeline import build_chunks, chunk_text, ingest
from rag_core.store import open_collection
from tests.conftest import fake_embed


def test_chunk_text_returns_overlapping_windows():
    assert chunk_text("abcdefghij", size=4, overlap=1) == ["abcd", "defg", "ghij"]


def test_chunk_text_keeps_short_text_as_one_chunk():
    assert chunk_text("short", size=100, overlap=10) == ["short"]


def test_chunk_text_returns_nothing_for_blank_text():
    assert chunk_text("   \n  ", size=4, overlap=1) == []


def test_build_chunks_skips_unsupported_files_and_uses_stable_ids(docs_dir):
    chunks = build_chunks(docs_dir, size=40, overlap=10)

    assert {c.source for c in chunks} == {"leave.md", "remote.md", "travel.txt"}
    assert chunks[0].id == f"{chunks[0].source}#0"


def test_ingest_rejects_empty_input_and_keeps_existing_store(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed)

    with pytest.raises(ValueError):
        ingest([], settings, fake_embed)

    assert open_collection(settings).count() > 0


def test_ingestion_eval_passes_after_ingest(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed)

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), ["leave.md"])

    assert report["passed"] is True
    assert report["stored_chunks"] == report["expected_chunks"]


def test_ingestion_eval_flags_store_out_of_date_with_docs(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed)
    (docs_dir / "remote.md").unlink()

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])

    assert report["passed"] is False
    assert all(chunk_id.startswith("remote.md#") for chunk_id in report["stale_chunks"])


def test_reingest_removes_chunks_of_deleted_docs(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed)
    (docs_dir / "remote.md").unlink()
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed)

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])

    assert report["passed"] is True
