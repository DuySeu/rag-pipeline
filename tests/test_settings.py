from __future__ import annotations

from pathlib import Path

import pytest

from core.config import load_settings
from core.constants import CONFIG_DEFAULTS, CONFIG_PATH
from core.errors import CliError, ErrorKind

CONFIG = """
chroma_dir: store
collection: docs
chunk_strategy: heading
chunk_size: 500
chunk_overlap: 50
top_k: 3
"""


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_settings_reads_config_file_key_from_env_and_models_from_arguments(tmp_path):
    settings = load_settings("embed", "chat", write_config(tmp_path, CONFIG))

    assert (settings.api_key, settings.embed_model, settings.chat_model) == ("k", "embed", "chat")
    assert settings.chroma_dir == Path("store")
    assert settings.chunk_strategy == "heading"
    assert (settings.chunk_size, settings.chunk_overlap, settings.top_k) == (500, 50, 3)


def test_load_settings_falls_back_to_defaults_for_empty_values(tmp_path):
    config = (
        CONFIG.replace("top_k: 3", "top_k:")
        .replace("collection: docs", 'collection: "  "')
        .replace("chunk_strategy: heading", "chunk_strategy: null")
        .replace("chroma_dir: store", "chroma_dir:")
    )

    settings = load_settings("embed", "chat", write_config(tmp_path, config))

    assert settings.top_k == CONFIG_DEFAULTS["top_k"]
    assert settings.collection == CONFIG_DEFAULTS["collection"]
    assert settings.chunk_strategy == CONFIG_DEFAULTS["chunk_strategy"]
    assert settings.chroma_dir == Path(str(CONFIG_DEFAULTS["chroma_dir"]))


@pytest.mark.parametrize("value", ["", "   "])
def test_load_settings_stops_with_config_error_when_api_key_is_empty(tmp_path, monkeypatch, value):
    monkeypatch.setenv("OPENROUTER_API_KEY", value)

    with pytest.raises(CliError, match="OPENROUTER_API_KEY is empty") as error:
        load_settings("embed", "chat", write_config(tmp_path, CONFIG))

    assert error.value.kind == ErrorKind.CONFIG


def test_load_settings_rejects_missing_and_unknown_keys(tmp_path):
    config = CONFIG.replace("top_k: 3", "topk: 3")

    with pytest.raises(ValueError, match=r"missing keys \['top_k'\], unknown keys \['topk'\]"):
        load_settings("embed", "chat", write_config(tmp_path, config))


def test_load_settings_rejects_a_file_that_is_not_a_mapping(tmp_path):
    with pytest.raises(TypeError, match="must map setting names to values"):
        load_settings("embed", "chat", write_config(tmp_path, "- chunk_size\n"))


def test_load_settings_rejects_overlap_not_smaller_than_chunk_size(tmp_path):
    config = CONFIG.replace("chunk_overlap: 50", "chunk_overlap: 500")

    with pytest.raises(ValueError, match="chunk_overlap must be"):
        load_settings("embed", "chat", write_config(tmp_path, config))


def test_repo_config_file_loads():
    assert load_settings("embed", "chat", CONFIG_PATH).chunk_size > 0
