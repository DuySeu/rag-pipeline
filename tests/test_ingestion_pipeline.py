from __future__ import annotations

from dataclasses import replace

import pytest

from core.store import open_collection
from evaluation.ingestion_eval import evaluate_ingestion
from ingestion.chunking import chunk_text
from ingestion.corpus import corpus_hash
from ingestion.loaders import Section
from ingestion.pipeline import build_chunks, chunk_documents, ingest
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
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))

    with pytest.raises(ValueError):
        ingest([], settings, fake_embed, "")

    assert open_collection(settings).count() > 0


def test_ingestion_eval_passes_after_ingest(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), ["leave.md"])

    assert report["passed"] is True
    assert report["stored_chunks"] == report["expected_chunks"]


def test_ingestion_eval_flags_store_out_of_date_with_docs(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))
    (docs_dir / "remote.md").unlink()

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])

    assert report["passed"] is False
    assert all(chunk_id.startswith("remote.md#") for chunk_id in report["stale_chunks"])


def test_reingest_removes_chunks_of_deleted_docs(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))
    (docs_dir / "remote.md").unlink()
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])

    assert report["passed"] is True


def test_chunk_ids_run_on_across_sections_and_carry_locators():
    documents = {
        "a.pdf": [Section("p1", {"page": 1}), Section("", {"page": 2}), Section("p3", {"page": 3})]
    }

    chunks = chunk_documents(documents, size=40, overlap=10)

    assert [(c.id, c.location) for c in chunks] == [
        ("a.pdf#0", {"page": 1}),
        ("a.pdf#1", {"page": 3}),
    ]


def test_ingest_writes_locator_metadata_and_all_collection_config_keys(settings, tmp_path):
    (tmp_path / "a.md").write_text("leave policy", encoding="utf-8")
    ingest(build_chunks(tmp_path, 40, 10), settings, fake_embed, corpus_hash(tmp_path))

    collection = open_collection(settings)

    assert set(collection.metadata) >= {
        "embed_model",
        "chunk_size",
        "chunk_overlap",
        "chunk_strategy",
        "embed_passage_prefix",
        "corpus_hash",
    }
    assert collection.get()["metadatas"] == [{"source": "a.md", "index": 0}]


def test_ingest_embeds_with_passage_prefix_but_stores_plain_text(settings, tmp_path):
    (tmp_path / "a.md").write_text("leave policy", encoding="utf-8")
    embedded = []

    def embed(texts):
        embedded.extend(texts)
        return fake_embed(texts)

    ingest(build_chunks(tmp_path, 40, 10), settings, embed, corpus_hash(tmp_path))

    assert embedded == ["passage: leave policy"]
    assert open_collection(settings).get()["documents"] == ["leave policy"]


def test_ingestion_eval_fails_on_chunk_size_mismatch_and_on_edited_file(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))
    collection = open_collection(settings)

    other = replace(settings, chunk_size=41)
    assert evaluate_ingestion(docs_dir, other, collection, [])["config_mismatch"] == {
        "chunk_size": {"store": 40, "settings": 41}
    }

    (docs_dir / "leave.md").write_text("leave policy: twelve leave dayz per year", encoding="utf-8")
    report = evaluate_ingestion(docs_dir, settings, collection, [])
    assert report["corpus_hash_mismatch"] is True
    assert report["passed"] is False
