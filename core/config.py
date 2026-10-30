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
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    top_k: int


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

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    # Reject a typo or a missing key instead of running with a silent default
    if not isinstance(config, dict):
        raise TypeError(f"{config_path} must map setting names to values")
    missing, unknown = (
        CONFIG_DEFAULTS.keys() - config.keys(),
        config.keys() - CONFIG_DEFAULTS.keys(),
    )
    if missing or unknown:
        raise ValueError(
            f"{config_path}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}"
        )

    # A key left empty (`key:`, null or a blank string) takes its default
    config = {
        key: CONFIG_DEFAULTS[key]
        if value is None or (isinstance(value, str) and not value.strip())
        else value
        for key, value in config.items()
    }

    settings = Settings(
        api_key=env["OPENROUTER_API_KEY"],
        embed_model=embed_model,
        chat_model=chat_model,
        chroma_dir=Path(config["chroma_dir"]),
        collection=str(config["collection"]),
        chunk_strategy=str(config["chunk_strategy"]),
        chunk_size=int(config["chunk_size"]),
        chunk_overlap=int(config["chunk_overlap"]),
        top_k=int(config["top_k"]),
    )

    # Reject a window that would never advance
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_size")

    return settings
