from __future__ import annotations

from pathlib import Path

import pytest

from core.config import load_settings
from core.constants import CONFIG_DEFAULTS, CONFIG_PATH
from core.errors import CliError, ErrorKind

CONFIG = """
store:
  chroma_dir: store
  collection: docs
ingestion:
  docs_dir: corpus
  chunk_strategy: heading
  chunk_size: 500
  chunk_overlap: 50
retrieval:
  top_k: 3
evaluation:
  golden_path: golden.jsonl
  reports_dir: reports
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
    assert settings.docs_dir == Path("corpus")
    assert (settings.golden_path, settings.reports_dir) == (Path("golden.jsonl"), Path("reports"))
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

    assert settings.top_k == CONFIG_DEFAULTS["retrieval"]["top_k"]
    assert settings.collection == CONFIG_DEFAULTS["store"]["collection"]
    assert settings.chunk_strategy == CONFIG_DEFAULTS["ingestion"]["chunk_strategy"]
    assert settings.chroma_dir == Path(str(CONFIG_DEFAULTS["store"]["chroma_dir"]))


@pytest.mark.parametrize("value", ["", "   "])
def test_load_settings_stops_with_config_error_when_api_key_is_empty(tmp_path, monkeypatch, value):
    monkeypatch.setenv("OPENROUTER_API_KEY", value)

    with pytest.raises(CliError, match="OPENROUTER_API_KEY is empty") as error:
        load_settings("embed", "chat", write_config(tmp_path, CONFIG))

    assert error.value.kind == ErrorKind.CONFIG


def test_load_settings_rejects_missing_and_unknown_keys(tmp_path):
    config = CONFIG.replace("top_k: 3", "topk: 3")

    with pytest.raises(CliError, match=r"missing keys \['retrieval.top_k'\], unknown keys \['retrieval.topk'\]"):
        load_settings("embed", "chat", write_config(tmp_path, config))


def test_load_settings_rejects_a_file_that_is_not_a_mapping(tmp_path):
    with pytest.raises(CliError, match="must map each section"):
        load_settings("embed", "chat", write_config(tmp_path, "- chunk_size\n"))


def test_load_settings_rejects_a_flat_key_outside_any_section(tmp_path):
    with pytest.raises(CliError, match="must map each section"):
        load_settings("embed", "chat", write_config(tmp_path, CONFIG + "top_k: 3\n"))


def test_load_settings_rejects_a_key_in_the_wrong_section(tmp_path):
    config = CONFIG.replace("retrieval:\n  top_k: 3", "retrieval:\n  chunk_size: 3").replace(
        "  chunk_size: 500\n", "  top_k: 500\n"
    )

    with pytest.raises(CliError, match="missing keys"):
        load_settings("embed", "chat", write_config(tmp_path, config))


def test_load_settings_rejects_overlap_not_smaller_than_chunk_size(tmp_path):
    config = CONFIG.replace("chunk_overlap: 50", "chunk_overlap: 500")

    with pytest.raises(CliError, match="chunk_overlap must be"):
        load_settings("embed", "chat", write_config(tmp_path, config))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("top_k: 3", "top_k: 0", "top_k must be at least 1"),
        ("chunk_size: 500", "chunk_size: many", "invalid literal for int"),
        ("chunk_size: 500", "chunk_size: [500]", "int\\(\\) argument"),
        ("store:", "store: [", "Cannot read"),
    ],
)
def test_load_settings_stops_with_config_error_on_bad_values(tmp_path, old, new, message):
    with pytest.raises(CliError, match=message) as error:
        load_settings("embed", "chat", write_config(tmp_path, CONFIG.replace(old, new, 1)))

    assert error.value.kind == ErrorKind.CONFIG


def test_load_settings_stops_with_config_error_when_file_is_missing(tmp_path):
    with pytest.raises(CliError, match="Cannot read") as error:
        load_settings("embed", "chat", tmp_path / "absent.yaml")

    assert error.value.kind == ErrorKind.CONFIG


def test_repo_config_file_loads():
    assert load_settings("embed", "chat", CONFIG_PATH).chunk_size > 0
