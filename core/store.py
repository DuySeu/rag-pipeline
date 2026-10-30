from __future__ import annotations

from collections.abc import Mapping

import chromadb
from chromadb.api.models.Collection import Collection

from core.config import Settings
from core.constants import COLLECTION_CONFIG
from core.errors import CliError, ErrorKind


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
    if metadata["embed_model"] != settings.embed_model:
        raise CliError(
            ErrorKind.CONFIG,
            f"Store was built with embed_model={metadata['embed_model']!r} but the setting is "
            f"{settings.embed_model!r}; re-run `python main.py ingest` or change the setting",
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
