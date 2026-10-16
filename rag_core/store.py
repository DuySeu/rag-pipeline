from __future__ import annotations

import chromadb
from chromadb.api.models.Collection import Collection

from rag_core.settings import Settings

COLLECTION_CONFIG = {"hnsw": {"space": "cosine"}}


# Open the persisted collection that ingestion writes and retrieval reads
def open_collection(settings: Settings) -> Collection:
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client.get_or_create_collection(
        settings.collection, embedding_function=None, configuration=COLLECTION_CONFIG
    )


# Drop the collection and return an empty one with the same name
def recreate_collection(settings: Settings) -> Collection:
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    if settings.collection in [c.name for c in client.list_collections()]:
        client.delete_collection(settings.collection)
    return client.create_collection(
        settings.collection, embedding_function=None, configuration=COLLECTION_CONFIG
    )
