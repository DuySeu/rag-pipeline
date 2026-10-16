from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from chromadb.api.models.Collection import Collection
from openai import OpenAI

from rag_core.openrouter import Embedder

ANSWER_SYSTEM_PROMPT = (
    "Answer the question using only the context below. "
    "If the context does not contain the answer, say you don't know. "
    "Answer in the same language as the question."
)


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    text: str
    distance: float


# Fetch the top_k chunks closest to the question
def retrieve(
    question: str, collection: Collection, embed: Embedder, top_k: int
) -> list[RetrievedChunk]:
    if collection.count() == 0:
        raise SystemExit("Vector store is empty; run `python -m ingestion` first")

    result = collection.query(
        query_embeddings=embed([question]),
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    return [
        RetrievedChunk(source=str(meta["source"]), text=text, distance=distance)
        for text, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
        )
    ]


# Ask the chat model to answer from the retrieved chunks
def generate_answer(
    question: str, chunks: Sequence[RetrievedChunk], client: OpenAI, model: str
) -> str:
    context = "\n\n".join(f"[{c.source}]\n{c.text}" for c in chunks)
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
    )
    return response.choices[0].message.content or ""
