from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import chromadb
import httpx
import openai
import yaml
from chromadb.api.models.Collection import Collection
from openai import AsyncOpenAI, OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBED_BATCH_SIZE = 64
MAX_RETRIES = 2
KEY_INFO_TIMEOUT = 10.0
COLLECTION_CONFIG = {"hnsw": {"space": "cosine"}}
CONFIG_PATH = Path("config.yaml")
# Settings read from CONFIG_PATH, with the value used when a key is left empty
CONFIG_DEFAULTS: dict[str, str | int | None] = {
    "chroma_dir": "data/chroma",
    "collection": "docs",
    "chunk_strategy": "fixed",
    "chunk_size": 800,
    "chunk_overlap": 100,
    "top_k": 4,
    "max_distance": None,
    "embed_query_prefix": "query: ",
    "embed_passage_prefix": "passage: ",
}
# Keys where "" is a real value (embed without a prefix) rather than an empty one
BLANK_STRING_KEYS = frozenset({"embed_query_prefix", "embed_passage_prefix"})
# Secrets and model names; these have no default and must be set, normally through .env
REQUIRED_ENV = ("OPENROUTER_API_KEY", "EMBED_MODEL", "CHAT_MODEL")


# Exit codes shared by the CLIs; 1 and 2 stay reserved for crashes and argparse
class ErrorKind(IntEnum):
    CONFIG = 5
    RATE_LIMIT = 6
    QUOTA = 7
    API = 8


# An expected failure that a CLI prints as `[ERROR <kind>] <message>` before exiting
class CliError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


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
    embed_query_prefix: str
    embed_passage_prefix: str
    max_distance: float | None


Embedder = Callable[[Sequence[str]], list[list[float]]]


# Read the key and model names from the environment and the rest from the YAML config, once at startup
def load_settings(config_path: Path = CONFIG_PATH) -> Settings:
    # Stop when a secret or model name is empty; guessing one would spend quota on the wrong model
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
        if value is None
        or (isinstance(value, str) and not value.strip() and key not in BLANK_STRING_KEYS)
        else value
        for key, value in config.items()
    }

    settings = Settings(
        api_key=env["OPENROUTER_API_KEY"],
        embed_model=env["EMBED_MODEL"],
        chat_model=env["CHAT_MODEL"],
        chroma_dir=Path(config["chroma_dir"]),
        collection=str(config["collection"]),
        chunk_strategy=str(config["chunk_strategy"]),
        chunk_size=int(config["chunk_size"]),
        chunk_overlap=int(config["chunk_overlap"]),
        top_k=int(config["top_k"]),
        embed_query_prefix=str(config["embed_query_prefix"]),
        embed_passage_prefix=str(config["embed_passage_prefix"]),
        max_distance=None if config["max_distance"] is None else float(config["max_distance"]),
    )

    # Reject a window that would never advance
    if not 0 <= settings.chunk_overlap < settings.chunk_size:
        raise ValueError("chunk_overlap must be >= 0 and smaller than chunk_size")

    return settings


# Build a sync OpenRouter client, failing early when the key is missing
def make_client(settings: Settings) -> OpenAI:
    if not settings.api_key:
        raise CliError(
            ErrorKind.CONFIG,
            "OPENROUTER_API_KEY is not set (copy .env.example to .env)",
        )
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=settings.api_key, max_retries=MAX_RETRIES)


# Build an async OpenRouter client for RAGAS, which scores through async calls
def make_async_client(settings: Settings) -> AsyncOpenAI:
    if not settings.api_key:
        raise CliError(
            ErrorKind.CONFIG,
            "OPENROUTER_API_KEY is not set (copy .env.example to .env)",
        )
    return AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL, api_key=settings.api_key, max_retries=MAX_RETRIES
    )


# Map an SDK error (raised after the SDK's own retries) to a CLI error, checking the daily quota on 429
def classify_api_error(client: OpenAI, exc: openai.APIError) -> CliError:
    status = getattr(exc, "status_code", None)
    detail = f"HTTP {status}: {exc.message}" if status else exc.message

    if status == 401:
        return CliError(ErrorKind.CONFIG, f"OpenRouter rejected the API key ({detail})")
    if status == 402:
        return CliError(ErrorKind.QUOTA, f"Out of OpenRouter credit ({detail})")
    if status != 429:
        return CliError(ErrorKind.API, detail)

    # A 429 is either a per-minute limit or the free-tier daily limit; /key tells them apart
    try:
        response = httpx.get(
            str(client.base_url.join("key")),
            headers={"Authorization": f"Bearer {client.api_key}"},
            timeout=KEY_INFO_TIMEOUT,
        )
        body = response.json()
        daily = (body.get("data") or body)["free_model_daily_requests"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return CliError(
            ErrorKind.RATE_LIMIT,
            f"{detail}. Cannot tell a per-minute limit from the daily limit; retry in a few minutes",
        )

    if daily.get("remaining") == 0:
        return CliError(
            ErrorKind.QUOTA,
            f"Free-tier daily limit reached ({daily.get('used')}/{daily.get('limit')} requests). "
            "Wait for the next UTC day or add credit",
        )
    return CliError(ErrorKind.RATE_LIMIT, f"{detail}. Retry in a few minutes")


# Return a function that embeds texts in batches with one model, prepending prefix to each text
def make_embedder(client: OpenAI, model: str, prefix: str = "") -> Embedder:
    def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [prefix + text for text in texts[start : start + EMBED_BATCH_SIZE]]
            try:
                response = client.embeddings.create(model=model, input=batch)
            except openai.APIError as exc:
                raise classify_api_error(client, exc) from exc
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors

    return embed


# Open the persisted collection that ingestion writes and retrieval reads
def open_collection(settings: Settings) -> Collection:
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client.get_or_create_collection(
        settings.collection, embedding_function=None, configuration=COLLECTION_CONFIG
    )


# Stop before any embed call when the store is empty or was built with other embed settings
def check_collection(collection: Collection, settings: Settings) -> None:
    if collection.count() == 0:
        raise CliError(ErrorKind.CONFIG, "Vector store is empty; run `python main.py ingest` first")

    metadata = collection.metadata or {}
    if "embed_model" not in metadata:
        raise CliError(
            ErrorKind.CONFIG,
            "Store has no embed_model metadata; re-run `python main.py ingest`",
        )
    for key, current in (
        ("embed_model", settings.embed_model),
        ("embed_query_prefix", settings.embed_query_prefix),
    ):
        if metadata.get(key) != current:
            raise CliError(
                ErrorKind.CONFIG,
                f"Store was built with {key}={metadata.get(key)!r} but the setting is "
                f"{current!r}; re-run `python main.py ingest` or change the setting",
            )


# Drop the collection and return an empty one with the same name, recording metadata on it
def recreate_collection(settings: Settings, metadata: Mapping[str, str | int]) -> Collection:
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    if settings.collection in [c.name for c in client.list_collections()]:
        client.delete_collection(settings.collection)
    return client.create_collection(
        settings.collection,
        embedding_function=None,
        configuration=COLLECTION_CONFIG,
        metadata=dict(metadata),
    )
