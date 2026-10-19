from __future__ import annotations

from collections.abc import Callable, Sequence

import httpx
import openai
from openai import AsyncOpenAI, OpenAI

from rag_core.errors import CliError, ErrorKind
from rag_core.settings import Settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBED_BATCH_SIZE = 64
MAX_RETRIES = 2
KEY_INFO_TIMEOUT = 10.0

Embedder = Callable[[Sequence[str]], list[list[float]]]


# Build a sync OpenRouter client, failing early when the key is missing
def make_client(settings: Settings) -> OpenAI:
    if not settings.api_key:
        raise CliError(
            ErrorKind.CONFIG, "OPENROUTER_API_KEY is not set (copy .env.example to .env)"
        )
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=settings.api_key, max_retries=MAX_RETRIES)


# Build an async OpenRouter client for RAGAS, which scores through async calls
def make_async_client(settings: Settings) -> AsyncOpenAI:
    if not settings.api_key:
        raise CliError(
            ErrorKind.CONFIG, "OPENROUTER_API_KEY is not set (copy .env.example to .env)"
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
