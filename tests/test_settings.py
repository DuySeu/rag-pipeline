from __future__ import annotations

from pathlib import Path

import pytest

from core import CONFIG_DEFAULTS, CONFIG_PATH, CliError, ErrorKind, load_settings

CONFIG = """
chroma_dir: store
collection: docs
chunk_strategy: heading
chunk_size: 500
chunk_overlap: 50
top_k: 3
max_distance: 0.6
embed_query_prefix: "query: "
embed_passage_prefix: "passage: "
"""


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("EMBED_MODEL", "embed")
    monkeypatch.setenv("CHAT_MODEL", "chat")


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_settings_reads_config_file_and_key_and_models_from_env(tmp_path):
    settings = load_settings(write_config(tmp_path, CONFIG))

    assert (settings.api_key, settings.embed_model, settings.chat_model) == ("k", "embed", "chat")
    assert settings.chroma_dir == Path("store")
    assert settings.chunk_strategy == "heading"
    assert (settings.chunk_size, settings.chunk_overlap, settings.top_k) == (500, 50, 3)
    assert settings.max_distance == 0.6
    assert settings.embed_query_prefix == "query: "


def test_load_settings_reads_null_max_distance_as_no_filter(tmp_path):
    config = CONFIG.replace("max_distance: 0.6", "max_distance: null")

    assert load_settings(write_config(tmp_path, config)).max_distance is None


def test_load_settings_falls_back_to_defaults_for_empty_values(tmp_path):
    config = (
        CONFIG.replace("top_k: 3", "top_k:")
        .replace("collection: docs", 'collection: "  "')
        .replace("chunk_strategy: heading", "chunk_strategy: null")
        .replace("chroma_dir: store", "chroma_dir:")
    )

    settings = load_settings(write_config(tmp_path, config))

    assert settings.top_k == CONFIG_DEFAULTS["top_k"]
    assert settings.collection == CONFIG_DEFAULTS["collection"]
    assert settings.chunk_strategy == CONFIG_DEFAULTS["chunk_strategy"]
    assert settings.chroma_dir == Path(str(CONFIG_DEFAULTS["chroma_dir"]))


def test_load_settings_keeps_a_blank_prefix_but_defaults_an_empty_one(tmp_path):
    config = CONFIG.replace('embed_query_prefix: "query: "', 'embed_query_prefix: ""').replace(
        'embed_passage_prefix: "passage: "', "embed_passage_prefix:"
    )

    settings = load_settings(write_config(tmp_path, config))

    assert settings.embed_query_prefix == ""
    assert settings.embed_passage_prefix == CONFIG_DEFAULTS["embed_passage_prefix"]


@pytest.mark.parametrize("value", ["", "   "])
def test_load_settings_stops_with_config_error_when_env_values_are_empty(
    tmp_path, monkeypatch, value
):
    monkeypatch.setenv("OPENROUTER_API_KEY", value)
    monkeypatch.delenv("CHAT_MODEL")

    with pytest.raises(CliError, match="OPENROUTER_API_KEY, CHAT_MODEL is empty") as error:
        load_settings(write_config(tmp_path, CONFIG))

    assert error.value.kind == ErrorKind.CONFIG


def test_load_settings_rejects_missing_and_unknown_keys(tmp_path):
    config = CONFIG.replace("top_k: 3", "topk: 3")

    with pytest.raises(ValueError, match=r"missing keys \['top_k'\], unknown keys \['topk'\]"):
        load_settings(write_config(tmp_path, config))


def test_load_settings_rejects_a_file_that_is_not_a_mapping(tmp_path):
    with pytest.raises(TypeError, match="must map setting names to values"):
        load_settings(write_config(tmp_path, "- chunk_size\n"))


def test_load_settings_rejects_overlap_not_smaller_than_chunk_size(tmp_path):
    config = CONFIG.replace("chunk_overlap: 50", "chunk_overlap: 500")

    with pytest.raises(ValueError, match="chunk_overlap must be"):
        load_settings(write_config(tmp_path, config))


def test_repo_config_file_loads():
    assert load_settings(CONFIG_PATH).chunk_size > 0
