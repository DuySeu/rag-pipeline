from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import openai
from chromadb.api.models.Collection import Collection
from openai import OpenAI

from core import Embedder, Settings, check_collection, classify_api_error

REFUSAL_ANSWER = "Không tìm thấy thông tin trong tài liệu."
CLARIFY_MARKER = "CLARIFY:"
LOCATOR_KEYS = ("page", "sheet", "slide")
CITATION_PATTERN = re.compile(r"\[(\d+)\]")

ANSWER_SYSTEM_PROMPT = (
    "Answer the question using only the numbered context below. "
    "If the context does not contain the answer, say you don't know. "
    "Answer in the same language as the question. "
    "After each statement taken from context chunk n, put the marker [n]. "
    "Reply with a JSON object: "
    '{"type": "answer" or "clarify", "text": "...", "citations": [n, ...]}, '
    "where citations lists the chunk numbers you used. "
    'Use type "clarify" only when the question matches several different things in the context '
    "(for example two policies with the same name in different documents) and each reading "
    "gives a different answer, so one merged answer would mislead. "
    "If a few cases can be answered together, answer all of them instead of asking. "
    'For "clarify", text is one short follow-up question in the language of the question, '
    "and citations lists the chunks that show the ambiguity. "
    f'If you cannot produce JSON, start a clarifying question with "{CLARIFY_MARKER}".'
)

Citation = tuple[str, str | int | None]


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    text: str
    distance: float
    locator: str | int | None = None


@dataclass(frozen=True)
class Answer:
    kind: str  # "answer", "clarify" or "refuse"
    text: str
    citations: list[Citation] = field(default_factory=list)
    usage: dict[str, int] | None = None
    latency_ms: dict[str, int] = field(default_factory=dict)
    invalid_citations: int = 0


# Fetch the top_k chunks closest to the question, before any distance threshold
def retrieve(
    question: str, collection: Collection, embed: Embedder, settings: Settings
) -> list[RetrievedChunk]:
    check_collection(collection, settings)

    result = collection.query(
        query_embeddings=embed([question]),
        n_results=settings.top_k,
        include=["documents", "metadatas", "distances"],
    )
    return [
        RetrievedChunk(
            source=str(meta["source"]),
            text=text,
            distance=distance,
            locator=next((meta[k] for k in LOCATOR_KEYS if k in meta), None),
        )
        for text, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        )
    ]


# Drop chunks farther than max_distance; no threshold keeps everything
def within_threshold(
    chunks: Sequence[RetrievedChunk], max_distance: float | None
) -> list[RetrievedChunk]:
    if max_distance is None:
        return list(chunks)
    return [c for c in chunks if c.distance <= max_distance]


# Read the chat reply as JSON, falling back to the CLARIFY: marker or plain text
def parse_reply(content: str) -> tuple[str, str, list[int]]:
    try:
        reply = json.loads(content)
    except json.JSONDecodeError:
        reply = None

    if isinstance(reply, dict) and isinstance(reply.get("text"), str):
        text = reply["text"]
        kind = "clarify" if reply.get("type") == "clarify" and text.strip() else "answer"
        cited = reply.get("citations")
        if isinstance(cited, list):
            numbers = [n for n in cited if isinstance(n, int) and not isinstance(n, bool)]
        else:
            numbers = [int(n) for n in CITATION_PATTERN.findall(text)]
        return kind, text, numbers

    if content.startswith(CLARIFY_MARKER):
        text = content.removeprefix(CLARIFY_MARKER).strip()
        return "clarify", text, [int(n) for n in CITATION_PATTERN.findall(text)]
    return "answer", content, [int(n) for n in CITATION_PATTERN.findall(content)]


# Ask the chat model to answer from the retrieved chunks, with citations or a clarifying question
def generate_answer(
    question: str, chunks: Sequence[RetrievedChunk], client: OpenAI, model: str
) -> Answer:
    context = "\n\n".join(
        f"[{n}] ({c.source}{'' if c.locator is None else f', {c.locator}'})\n{c.text}"
        for n, c in enumerate(chunks, start=1)
    )
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ],
        )
    except openai.APIError as exc:
        raise classify_api_error(client, exc) from exc
    latency = round((time.perf_counter() - started) * 1000)

    kind, text, numbers = parse_reply(response.choices[0].message.content or "")

    # Map [n] back to chunks; numbers outside the context are dropped and counted
    citations: list[Citation] = []
    for n in dict.fromkeys(numbers):
        if 1 <= n <= len(chunks):
            citation = (chunks[n - 1].source, chunks[n - 1].locator)
            if citation not in citations:
                citations.append(citation)

    usage: Any = response.usage
    return Answer(
        kind=kind,
        text=text,
        citations=citations,
        usage=(
            {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
            if usage
            else None
        ),
        latency_ms={"generate": latency},
        invalid_citations=sum(not 1 <= n <= len(chunks) for n in dict.fromkeys(numbers)),
    )


# Retrieve, refuse when nothing is close enough, otherwise answer; also return the unfiltered chunks
def ask(
    question: str, collection: Collection, embed: Embedder, client: OpenAI, settings: Settings
) -> tuple[list[RetrievedChunk], Answer]:
    started = time.perf_counter()
    chunks = retrieve(question, collection, embed, settings)
    retrieve_ms = round((time.perf_counter() - started) * 1000)

    kept = within_threshold(chunks, settings.max_distance)
    if not kept:
        return chunks, Answer("refuse", REFUSAL_ANSWER, latency_ms={"retrieve": retrieve_ms})

    answer = generate_answer(question, kept, client, settings.chat_model)
    return chunks, replace(answer, latency_ms={"retrieve": retrieve_ms, **answer.latency_ms})
