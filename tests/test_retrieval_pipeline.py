from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

import retrieval.pipeline
from core.errors import CliError, ErrorKind
from core.store import open_collection
from ingestion.corpus import corpus_hash
from ingestion.pipeline import build_chunks, ingest
from retrieval.pipeline import (
    REFUSAL_ANSWER,
    RetrievedChunk,
    ask,
    generate_answer,
    parse_reply,
    retrieve,
    within_threshold,
)
from tests.conftest import fake_embed


def build_store(settings, docs_dir):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))


def chat_client(content, usage=True, calls=None):
    def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5) if usage else None,
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


CHUNKS = [
    RetrievedChunk("a.pdf", "text a", 0.1, 3),
    RetrievedChunk("b.md", "text b", 0.2),
    RetrievedChunk("c.xlsx", "text c", 0.3, "Q1"),
    RetrievedChunk("d.md", "text d", 0.4),
]


def test_retrieve_returns_closest_source_first(settings, docs_dir):
    build_store(settings, docs_dir)

    chunks = retrieve("how many remote days?", open_collection(settings), fake_embed, settings)

    assert len(chunks) == 2
    assert chunks[0].source == "remote.md"


def test_retrieve_stops_when_store_is_empty(settings):
    with pytest.raises(CliError) as caught:
        retrieve("anything", open_collection(settings), fake_embed, settings)

    assert caught.value.kind is ErrorKind.CONFIG


def test_retrieve_embeds_question_with_query_prefix(settings, docs_dir):
    build_store(settings, docs_dir)
    embedded = []

    def embed(texts):
        embedded.extend(texts)
        return fake_embed(texts)

    retrieve("remote days", open_collection(settings), embed, settings)

    assert embedded == ["query: remote days"]


@pytest.mark.parametrize("field", ["embed_model"])
def test_retrieve_stops_before_embedding_when_store_settings_differ(settings, docs_dir, field):
    build_store(settings, docs_dir)
    embedded = []

    with pytest.raises(CliError) as caught:
        retrieve(
            "q",
            open_collection(settings),
            lambda texts: embedded.append(texts),
            replace(settings, **{field: "other"}),
        )

    assert caught.value.kind is ErrorKind.CONFIG
    assert field in str(caught.value)
    assert embedded == []


def test_retrieve_stops_when_store_has_no_metadata(settings, docs_dir):
    collection = open_collection(settings)
    collection.add(
        ids=["x#0"], documents=["t"], metadatas=[{"source": "x"}], embeddings=[[1, 0, 0]]
    )

    with pytest.raises(CliError, match="re-run"):
        retrieve("q", collection, fake_embed, settings)


def test_within_threshold_keeps_everything_without_a_threshold_and_filters_with_one():
    assert within_threshold(CHUNKS, None) == CHUNKS
    assert within_threshold(CHUNKS, 0.2) == CHUNKS[:2]


def test_ask_refuses_without_calling_chat_when_no_chunk_is_close_enough(
    settings, docs_dir, monkeypatch
):
    build_store(settings, docs_dir)
    monkeypatch.setattr(retrieval.pipeline, "MAX_DISTANCE", -1.0)
    calls = []

    chunks, answer = ask(
        "q",
        open_collection(settings),
        fake_embed,
        chat_client("{}", calls=calls),
        settings,
    )

    assert (answer.kind, answer.text, answer.citations) == ("refuse", REFUSAL_ANSWER, [])
    assert len(chunks) == 2
    assert calls == []


def test_ask_answers_with_both_latencies_when_a_chunk_passes(settings, docs_dir):
    build_store(settings, docs_dir)
    reply = '{"type": "answer", "text": "two [1]", "citations": [1]}'

    _, answer = ask(
        "remote days?", open_collection(settings), fake_embed, chat_client(reply), settings
    )

    assert answer.kind == "answer"
    assert answer.citations == [("remote.md", None)]
    assert set(answer.latency_ms) == {"retrieve", "generate"}


def test_generate_answer_maps_citations_and_counts_invalid_numbers():
    reply = '{"type": "answer", "text": "x [1] [3] [9]", "citations": [1, 3, 9, 1]}'

    answer = generate_answer("q", CHUNKS, chat_client(reply), "m")

    assert answer.citations == [("a.pdf", 3), ("c.xlsx", "Q1")]
    assert answer.invalid_citations == 1
    assert answer.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_generate_answer_numbers_context_with_source_and_locator():
    calls = []

    generate_answer("q", CHUNKS[:2], chat_client("hi", calls=calls), "m")

    user = calls[0]["messages"][1]["content"]
    assert "[1] (a.pdf, 3)\ntext a" in user
    assert "[2] (b.md)\ntext b" in user
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_generate_answer_returns_no_usage_when_provider_omits_it():
    assert generate_answer("q", CHUNKS, chat_client("hi", usage=False), "m").usage is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            '{"type": "clarify", "text": "Which one?", "citations": [1]}',
            ("clarify", "Which one?", [1]),
        ),
        ('{"type": "weird", "text": "ok [2]"}', ("answer", "ok [2]", [2])),
        ('{"type": "clarify", "text": "  "}', ("answer", "  ", [])),
        ("CLARIFY: Which one? [1]", ("clarify", "Which one? [1]", [1])),
        ("plain answer [2] and [4]", ("answer", "plain answer [2] and [4]", [2, 4])),
        ("", ("answer", "", [])),
    ],
)
def test_parse_reply_handles_json_marker_and_plain_text(content, expected):
    assert parse_reply(content) == expected
