from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.constants import (
    CONFIG_DEFAULTS,
    CONFIG_PATH,
    REQUIRED_ENV,
)
from core.errors import CliError, ErrorKind


@dataclass(frozen=True)
class Settings:
    api_key: str
    embed_model: str
    chat_model: str
    chroma_dir: Path
    collection: str
    docs_dir: Path
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    golden_path: Path
    reports_dir: Path


# Read the key from the environment and the rest from the YAML config; main.py picks the models
def load_settings(embed_model: str, chat_model: str, config_path: Path = CONFIG_PATH) -> Settings:
    # Stop when a secret is empty; there is nothing sensible to fall back to
    env = {name: os.environ.get(name, "").strip() for name in REQUIRED_ENV}
    empty = [name for name, value in env.items() if not value]
    if empty:
        raise CliError(
            ErrorKind.CONFIG,
            f"{', '.join(empty)} is empty or not set; fill it in .env (see .env.example)",
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CliError(ErrorKind.CONFIG, f"Cannot read {config_path} ({exc})") from exc

    # Flatten each pipeline section to `section.key` so one check covers sections and keys
    if not isinstance(raw, dict) or not all(
        isinstance(section, dict) for section in raw.values()
    ):
        raise CliError(
            ErrorKind.CONFIG, f"{config_path} must map each section to setting names and values"
        )
    found = {f"{name}.{key}": value for name, section in raw.items() for key, value in section.items()}
    defaults = {
        f"{name}.{key}": value
        for name, section in CONFIG_DEFAULTS.items()
        for key, value in section.items()
    }

    # Reject a typo or a missing key instead of running with a silent default
    missing, unknown = defaults.keys() - found.keys(), found.keys() - defaults.keys()
    if missing or unknown:
        raise CliError(
            ErrorKind.CONFIG,
            f"{config_path}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}",
        )

    # A key left empty (`key:`, null or a blank string) takes its default
    config = {
        key: defaults[key]
        if value is None or (isinstance(value, str) and not value.strip())
        else value
        for key, value in found.items()
    }

    # A number written as text that int() cannot read is a config error too
    try:
        settings = Settings(
            api_key=env["OPENROUTER_API_KEY"],
            embed_model=embed_model,
            chat_model=chat_model,
            chroma_dir=Path(config["store.chroma_dir"]),
            collection=str(config["store.collection"]),
            docs_dir=Path(config["ingestion.docs_dir"]),
            chunk_strategy=str(config["ingestion.chunk_strategy"]),
            chunk_size=int(config["ingestion.chunk_size"]),
            chunk_overlap=int(config["ingestion.chunk_overlap"]),
            top_k=int(config["retrieval.top_k"]),
            golden_path=Path(config["evaluation.golden_path"]),
            reports_dir=Path(config["evaluation.reports_dir"]),
        )
    except (TypeError, ValueError) as exc:
        raise CliError(ErrorKind.CONFIG, f"{config_path}: {exc}") from exc

    # Reject a window that would never advance and a query that asks for nothing
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise CliError(
            ErrorKind.CONFIG,
            f"{config_path}: chunk_overlap must be >= 0 and smaller than chunk_size",
        )
    if settings.top_k < 1:
        raise CliError(ErrorKind.CONFIG, f"{config_path}: top_k must be at least 1")

    return settings
