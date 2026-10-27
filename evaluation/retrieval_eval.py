from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ragas.embeddings.base import embedding_factory
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecisionWithReference,
    ContextRecall,
    Faithfulness,
)

from core.config import Settings
from core.openrouter import make_async_client, make_client, make_embedder
from core.store import open_collection
from evaluation.golden import load_golden
from retrieval.pipeline import generate_answer, retrieve

SAMPLES_PATH = Path("evaluation/reports/retrieval_samples.jsonl")
SUMMARY_PATH = Path("evaluation/reports/retrieval_summary.json")
RAGAS_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")

logger = logging.getLogger(__name__)


# Return the 1-based rank of the first retrieved chunk from an expected source
def first_hit_rank(retrieved_sources: Sequence[str], expected_sources: Sequence[str]) -> int | None:
    return next(
        (
            rank
            for rank, source in enumerate(retrieved_sources, start=1)
            if source in expected_sources
        ),
        None,
    )


# Average hit rate, MRR and each RAGAS metric over the scored samples
def summarize(rows: Sequence[dict[str, Any]], top_k: int) -> dict[str, Any]:
    ranks = [row["hit_rank"] for row in rows]
    summary: dict[str, Any] = {
        "samples": len(rows),
        f"hit_rate@{top_k}": round(sum(r is not None for r in ranks) / len(rows), 3),
        "mrr": round(statistics.mean(1 / r if r else 0.0 for r in ranks), 3),
    }
    for name in RAGAS_METRICS:
        summary[name] = round(statistics.mean(row[name] for row in rows), 3)
    return summary


# Retrieve, answer and score every golden question, writing rows as they finish
async def run(settings: Settings) -> dict[str, Any]:
    client = make_client(settings)
    async_client = make_async_client(settings)
    collection = open_collection(settings)
    embed = make_embedder(client, settings.embed_model, settings.embed_query_prefix)

    # The judge uses the same OpenRouter models as the pipeline
    judge = llm_factory(settings.chat_model, provider="openai", client=async_client)
    judge_embeddings = embedding_factory("openai", model=settings.embed_model, client=async_client)
    faithfulness = Faithfulness(llm=judge)
    answer_relevancy = AnswerRelevancy(llm=judge, embeddings=judge_embeddings)
    context_precision = ContextPrecisionWithReference(llm=judge)
    context_recall = ContextRecall(llm=judge)

    SAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLES_PATH.write_text("", encoding="utf-8")

    rows: list[dict[str, Any]] = []
    golden = load_golden()
    # ponytail: samples run one at a time to stay under free-tier rate limits, gather() if paid
    for number, sample in enumerate(golden, start=1):
        logger.info("Scoring %d/%d: %s", number, len(golden), sample.question)
        chunks = retrieve(sample.question, collection, embed, settings)
        answer = generate_answer(sample.question, chunks, client, settings.chat_model)
        contexts = [c.text for c in chunks]

        row = {
            "question": sample.question,
            "answer": answer.text,
            "retrieved_sources": [c.source for c in chunks],
            "expected_sources": list(sample.sources),
            "hit_rank": first_hit_rank([c.source for c in chunks], sample.sources),
            "faithfulness": (
                await faithfulness.ascore(
                    user_input=sample.question, response=answer.text, retrieved_contexts=contexts
                )
            ).value,
            "answer_relevancy": (
                await answer_relevancy.ascore(user_input=sample.question, response=answer.text)
            ).value,
            "context_precision": (
                await context_precision.ascore(
                    user_input=sample.question,
                    reference=sample.reference,
                    retrieved_contexts=contexts,
                )
            ).value,
            "context_recall": (
                await context_recall.ascore(
                    user_input=sample.question,
                    retrieved_contexts=contexts,
                    reference=sample.reference,
                )
            ).value,
        }
        rows.append(row)

        # Append each row now so a rate-limit failure keeps the finished samples
        with SAMPLES_PATH.open("a", encoding="utf-8") as samples_file:
            samples_file.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = summarize(rows, settings.top_k)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
