from __future__ import annotations

from collections.abc import Mapping

import chromadb
from chromadb.api.models.Collection import Collection

from rag_core.errors import CliError, ErrorKind
from rag_core.settings import Settings

COLLECTION_CONFIG = {"hnsw": {"space": "cosine"}}


# Open the persisted collection that ingestion writes and retrieval reads
def open_collection(settings: Settings) -> Collection:
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client.get_or_create_collection(
        settings.collection, embedding_function=None, configuration=COLLECTION_CONFIG
    )


# Stop before any embed call when the store is empty or was built with other embed settings
def check_collection(collection: Collection, settings: Settings) -> None:
    if collection.count() == 0:
        raise CliError(ErrorKind.CONFIG, "Vector store is empty; run `python -m ingestion` first")

    metadata = collection.metadata or {}
    if "embed_model" not in metadata:
        raise CliError(
            ErrorKind.CONFIG, "Store has no embed_model metadata; re-run `python -m ingestion`"
        )
    for key, current in (
        ("embed_model", settings.embed_model),
        ("embed_query_prefix", settings.embed_query_prefix),
    ):
        if metadata.get(key) != current:
            raise CliError(
                ErrorKind.CONFIG,
                f"Store was built with {key}={metadata.get(key)!r} but the setting is "
                f"{current!r}; re-run `python -m ingestion` or change the setting",
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
