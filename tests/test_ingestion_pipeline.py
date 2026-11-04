from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from core.store import delete_collection, open_collection
from evaluation.ingestion_eval import evaluate_ingestion
from ingestion.chunking import chunk_text
from ingestion.corpus import corpus_hash
from ingestion.loaders import Section
from ingestion.pipeline import (
    ChangePlan,
    build_chunks,
    chunk_documents,
    ingest,
    plan_changes,
)
from main import main
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


def test_ingest_rejects_empty_input_and_keeps_existing_store(settings, docs_dir, tmp_path):
    ingest(docs_dir, settings, fake_embed)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(ValueError):
        ingest(empty_dir, settings, fake_embed)

    assert open_collection(settings).count() > 0


def test_delete_collection_removes_an_ingested_store(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)

    assert delete_collection(settings) is True
    assert open_collection(settings).count() == 0


def test_delete_collection_returns_false_when_store_has_no_collection(settings):
    assert delete_collection(settings) is False


def test_delete_collection_command_drops_the_store_and_says_so(
    monkeypatch, settings, docs_dir, capsys
):
    ingest(docs_dir, settings, fake_embed)
    monkeypatch.setattr("main.load_settings", lambda *args: settings)
    monkeypatch.setattr("sys.argv", ["main.py", "delete-collection"])

    main()

    assert "Deleted collection docs" in capsys.readouterr().out
    assert delete_collection(settings) is False


def test_delete_collection_command_reports_a_missing_collection(monkeypatch, settings, capsys):
    monkeypatch.setattr("main.load_settings", lambda *args: settings)
    monkeypatch.setattr("sys.argv", ["main.py", "delete-collection"])

    main()

    assert "No collection docs" in capsys.readouterr().out


def test_ingestion_eval_passes_after_ingest(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), ["leave.md"])

    assert report["passed"] is True
    assert report["stored_chunks"] == report["expected_chunks"]


def test_ingestion_eval_flags_store_out_of_date_with_docs(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "remote.md").unlink()

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])

    assert report["passed"] is False
    assert all(chunk_id.startswith("remote.md#") for chunk_id in report["stale_chunks"])


def test_reingest_removes_chunks_of_deleted_docs(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "remote.md").unlink()
    ingest(docs_dir, settings, fake_embed)

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
    ingest(tmp_path, settings, fake_embed)

    collection = open_collection(settings)

    assert set(collection.metadata) >= {
        "embed_model",
        "chunk_size",
        "chunk_overlap",
        "chunk_strategy",
        "embed_passage_prefix",
        "corpus_hash",
    }
    file_hash = hashlib.sha256(b"leave policy").hexdigest()
    assert collection.get()["metadatas"] == [{"source": "a.md", "index": 0, "file_hash": file_hash}]


def test_ingest_embeds_with_passage_prefix_but_stores_plain_text(settings, tmp_path):
    (tmp_path / "a.md").write_text("leave policy", encoding="utf-8")
    embedded = []

    def embed(texts):
        embedded.extend(texts)
        return fake_embed(texts)

    ingest(tmp_path, settings, embed)

    assert embedded == ["passage: leave policy"]
    assert open_collection(settings).get()["documents"] == ["leave policy"]


def test_ingestion_eval_fails_on_chunk_size_mismatch_and_on_edited_file(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    collection = open_collection(settings)

    other = replace(settings, chunk_size=41)
    assert evaluate_ingestion(docs_dir, other, collection, [])["config_mismatch"] == {
        "chunk_size": {"store": 40, "settings": 41}
    }

    (docs_dir / "leave.md").write_text("leave policy: twelve leave dayz per year", encoding="utf-8")
    report = evaluate_ingestion(docs_dir, settings, collection, [])
    assert report["corpus_hash_mismatch"] is True
    assert report["passed"] is False


def recording_embed(calls):
    def embed(texts):
        calls.append(list(texts))
        return fake_embed(texts)

    return embed


def stored_sources(settings):
    return {m["source"] for m in open_collection(settings).get()["metadatas"]}


def test_plan_changes_splits_new_changed_removed_unchanged():
    plan = plan_changes(
        {"a.md": "1", "b.md": "2", "c.md": "3"}, {"a.md": "1", "b.md": "9", "d.md": "4"}
    )

    assert plan == ChangePlan(
        new=("d.md",), changed=("b.md",), removed=("c.md",), unchanged=("a.md",)
    )


def test_second_ingest_embeds_nothing_when_docs_are_unchanged(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    calls = []

    result = ingest(docs_dir, settings, recording_embed(calls))

    assert calls == []
    assert result.embedded_chunks == 0
    assert result.plan.unchanged == ("leave.md", "remote.md", "travel.txt")


def test_ingest_embeds_only_the_added_file(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "new.md").write_text("leave rules", encoding="utf-8")
    calls = []

    result = ingest(docs_dir, settings, recording_embed(calls))

    assert calls == [["passage: leave rules"]]
    assert result.plan.new == ("new.md",)
    assert "new.md" in stored_sources(settings)


def test_ingest_replaces_chunks_of_an_edited_file(settings, docs_dir):
    (docs_dir / "leave.md").write_text("leave policy " * 10, encoding="utf-8")
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "leave.md").write_text("leave short", encoding="utf-8")

    result = ingest(docs_dir, settings, fake_embed)

    stored = open_collection(settings).get(where={"source": "leave.md"})
    assert result.plan.changed == ("leave.md",)
    assert stored["ids"] == ["leave.md#0"]
    assert stored["documents"] == ["leave short"]


def test_ingest_drops_chunks_of_a_deleted_file(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "remote.md").unlink()

    ingest(docs_dir, settings, fake_embed)

    assert stored_sources(settings) == {"leave.md", "travel.txt"}


def test_ingest_with_only_deletions_makes_no_embed_call(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "remote.md").unlink()
    calls = []

    result = ingest(docs_dir, settings, recording_embed(calls))

    assert calls == []
    assert result.plan.removed == ("remote.md",)


def test_ingest_rebuilds_everything_when_chunk_settings_change(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    wider = replace(settings, chunk_size=60)
    calls = []

    result = ingest(docs_dir, wider, recording_embed(calls))

    collection = open_collection(wider)
    assert result.plan is None
    assert len(calls) == 1 and len(calls[0]) == collection.count()
    assert collection.metadata["chunk_size"] == 60


def test_ingest_rebuilds_store_without_file_hash_metadata(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    open_collection(settings).add(
        ids=["old.md#0"],
        documents=["old"],
        metadatas=[{"source": "old.md", "index": 0}],
        embeddings=fake_embed(["old"]),
    )

    result = ingest(docs_dir, settings, fake_embed)

    assert result.plan is None
    assert "old.md#0" not in open_collection(settings).get()["ids"]


def test_ingestion_eval_passes_after_incremental_ingest(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "leave.md").write_text("leave policy: ten leave days", encoding="utf-8")
    (docs_dir / "remote.md").unlink()
    (docs_dir / "new.md").write_text("travel rules", encoding="utf-8")

    ingest(docs_dir, settings, fake_embed)

    collection = open_collection(settings)
    assert collection.metadata["corpus_hash"] == corpus_hash(docs_dir)
    assert evaluate_ingestion(docs_dir, settings, collection, [])["passed"] is True


def test_incremental_embed_failure_leaves_store_unchanged(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    before = open_collection(settings)
    before_state = (before.get(), before.metadata)
    (docs_dir / "leave.md").write_text("leave policy: ten leave days", encoding="utf-8")

    def failing_embed(texts):
        raise RuntimeError("quota")

    with pytest.raises(RuntimeError):
        ingest(docs_dir, settings, failing_embed)

    after = open_collection(settings)
    assert (after.get(), after.metadata) == before_state


def test_corpus_hash_is_unchanged_by_refactor(docs_dir):
    digest = hashlib.sha256()
    for name in ("leave.md", "remote.md", "travel.txt"):
        file_hash = hashlib.sha256((docs_dir / name).read_bytes()).hexdigest()
        digest.update(f"{name}\0{file_hash}\n".encode())

    assert corpus_hash(docs_dir) == digest.hexdigest()


def test_ingest_skips_embed_when_new_file_has_no_chunks(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    count = open_collection(settings).count()
    (docs_dir / "empty.md").write_text("   ", encoding="utf-8")
    calls = []

    result = ingest(docs_dir, settings, recording_embed(calls))

    assert calls == []
    assert result.empty_files == ("empty.md",)
    assert open_collection(settings).count() == count


def test_ingestion_eval_passes_after_adding_an_empty_file(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "empty.md").write_text("   ", encoding="utf-8")

    ingest(docs_dir, settings, fake_embed)

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])
    assert report["passed"] is True


def test_ingestion_eval_passes_after_deleting_a_file_without_chunks(settings, docs_dir):
    (docs_dir / "empty.md").write_text("   ", encoding="utf-8")
    ingest(docs_dir, settings, fake_embed)
    (docs_dir / "empty.md").unlink()

    ingest(docs_dir, settings, fake_embed)

    report = evaluate_ingestion(docs_dir, settings, open_collection(settings), [])
    assert report["passed"] is True


def test_ingest_rebuilds_when_store_is_marked_dirty(settings, docs_dir):
    ingest(docs_dir, settings, fake_embed)
    collection = open_collection(settings)
    collection.modify(metadata={**collection.metadata, "corpus_hash": ""})
    calls = []

    result = ingest(docs_dir, settings, recording_embed(calls))

    assert result.plan is None
    assert len(calls[0]) == open_collection(settings).count()
