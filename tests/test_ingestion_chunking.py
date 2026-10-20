from __future__ import annotations

import pytest

from ingestion.chunking import STRATEGIES, chunk_headings, chunk_recursive
from ingestion.loaders import Section
from ingestion.pipeline import chunk_documents

LEGAL_TEXT = (
    "Chương I\nQUY ĐỊNH CHUNG\n"
    "Điều 1. Phạm vi\nNội dung một theo Điều 5\nĐiều 5 Nghị định này áp dụng.\n"
    "Điều 2. Đối tượng\nNội dung hai."
)


def test_recursive_keeps_sentences_whole_and_overlaps_by_whole_sentences():
    assert chunk_recursive("Câu một. Câu hai. Câu ba.", size=18, overlap=9) == [
        "Câu một. Câu hai.",
        "Câu hai. Câu ba.",
    ]


def test_recursive_prefers_paragraph_breaks():
    assert chunk_recursive("aaaa bbbb\n\ncccc dddd", size=10, overlap=0) == [
        "aaaa bbbb",
        "cccc dddd",
    ]


def test_recursive_hard_cuts_a_word_longer_than_size():
    assert chunk_recursive("x" * 25, size=10, overlap=0) == ["x" * 10, "x" * 10, "x" * 5]


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_every_strategy_keeps_chunks_within_size_and_drops_blank_text(strategy):
    text = "Điều 1. " + "lorem ipsum dolor sit amet. " * 40

    chunks = STRATEGIES[strategy](text, 100, 20)

    assert chunks
    assert all(0 < len(c) <= 100 for c in chunks)
    assert STRATEGIES[strategy]("  \n ", 100, 20) == []


def test_heading_starts_a_chunk_at_each_legal_heading_but_not_at_a_reference():
    assert chunk_headings(LEGAL_TEXT, size=200, overlap=0) == [
        "Chương I\nQUY ĐỊNH CHUNG",
        "Điều 1. Phạm vi\nNội dung một theo Điều 5\nĐiều 5 Nghị định này áp dụng.",
        "Điều 2. Đối tượng\nNội dung hai.",
    ]


def test_heading_splits_markdown_and_recursively_splits_a_long_section():
    chunks = chunk_headings("# A\nshort\n## B\n" + "word " * 10, size=20, overlap=5)

    assert chunks[0] == "# A\nshort"
    assert chunks[1].startswith("## B\n")
    assert all(len(c) <= 20 for c in chunks)


def test_chunk_documents_uses_the_strategy_but_still_packs_line_wise_sections():
    documents = {
        "law.pdf": [Section(LEGAL_TEXT, {"page": 1})],
        "rows.xlsx": [Section("a: 1\nb: 2", {"sheet": "S"}, line_wise=True)],
    }

    chunks = chunk_documents(documents, size=200, overlap=0, strategy="heading")

    assert [c.id for c in chunks] == ["law.pdf#0", "law.pdf#1", "law.pdf#2", "rows.xlsx#0"]
    assert chunks[-1].text == "a: 1\nb: 2"


def test_chunk_documents_rejects_an_unknown_strategy_listing_the_options():
    with pytest.raises(ValueError, match=r"'semantic'; use one of: fixed \| recursive \| heading"):
        chunk_documents({}, size=200, overlap=0, strategy="semantic")
