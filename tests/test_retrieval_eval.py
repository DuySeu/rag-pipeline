from __future__ import annotations

from pathlib import Path

from evaluation.golden import load_golden
from evaluation.retrieval_eval import first_hit_rank, summarize


def test_first_hit_rank_finds_first_expected_source():
    assert first_hit_rank(["a.md", "b.md", "b.md"], ["b.md"]) == 2


def test_first_hit_rank_returns_none_when_no_source_matches():
    assert first_hit_rank(["a.md"], ["b.md"]) is None


def test_summarize_averages_hit_rate_mrr_and_metrics():
    scores = {
        "faithfulness": 1.0,
        "answer_relevancy": 0.5,
        "context_precision": 1.0,
        "context_recall": 0.0,
    }
    rows = [{"hit_rank": 1, **scores}, {"hit_rank": 2, **scores}, {"hit_rank": None, **scores}]

    summary = summarize(rows, top_k=4)

    assert summary["hit_rate@4"] == 0.667
    assert summary["mrr"] == 0.5
    assert summary["answer_relevancy"] == 0.5


def test_golden_set_sources_exist_in_docs_folder():
    docs = {p.name for p in Path("data/docs").iterdir()}
    for sample in load_golden():
        assert set(sample.sources) <= docs, sample.question
