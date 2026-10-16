from __future__ import annotations

from collections.abc import Callable, Sequence

from openai import AsyncOpenAI, OpenAI

from rag_core.settings import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBED_BATCH_SIZE = 64

Embedder = Callable[[Sequence[str]], list[list[float]]]


# Build a sync OpenRouter client, failing early when the key is missing
def make_client(settings: Settings) -> OpenAI:
    if not settings.api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set (copy .env.example to .env)")
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=settings.api_key)


# Build an async OpenRouter client for RAGAS, which scores through async calls
def make_async_client(settings: Settings) -> AsyncOpenAI:
    if not settings.api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set (copy .env.example to .env)")
    return AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=settings.api_key)


# Return a function that embeds texts in batches with one model
def make_embedder(client: OpenAI, model: str) -> Embedder:
    def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = list(texts[start : start + EMBED_BATCH_SIZE])
            response = client.embeddings.create(model=model, input=batch)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors

    return embed
