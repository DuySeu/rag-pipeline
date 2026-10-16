from __future__ import annotations

import pytest

from ingestion.pipeline import build_chunks, ingest
from rag_core.store import open_collection
from retrieval.pipeline import retrieve
from tests.conftest import fake_embed


def test_retrieve_returns_closest_source_first(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed)

    chunks = retrieve("how many remote days?", open_collection(settings), fake_embed, top_k=2)

    assert len(chunks) == 2
    assert chunks[0].source == "remote.md"


def test_retrieve_stops_when_store_is_empty(settings):
    with pytest.raises(SystemExit):
        retrieve("anything", open_collection(settings), fake_embed, top_k=2)
