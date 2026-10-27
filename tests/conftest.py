from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from core.config import Settings

VOCABULARY = ("leave", "remote", "travel")


# Embed by counting vocabulary words so similar texts land close together
def fake_embed(texts: Sequence[str]) -> list[list[float]]:
    return [[text.lower().count(word) + 0.01 for word in VOCABULARY] for text in texts]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key="",
        embed_model="fake",
        chat_model="fake",
        chroma_dir=tmp_path / "chroma",
        collection="docs",
        chunk_strategy="fixed",
        chunk_size=40,
        chunk_overlap=10,
        top_k=2,
        embed_query_prefix="query: ",
        embed_passage_prefix="passage: ",
        max_distance=None,
    )


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "leave.md").write_text("leave policy: twelve leave days per year", encoding="utf-8")
    (docs / "remote.md").write_text("remote work: two remote days per week", encoding="utf-8")
    (docs / "travel.txt").write_text("travel allowance: travel meals paid daily", encoding="utf-8")
    (docs / "ignored.csv").write_text("not a supported file", encoding="utf-8")
    return docs
