from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from ingestion.__main__ import main
from ingestion.corpus import corpus_hash
from ingestion.pipeline import build_chunks, ingest
from rag_core import openrouter
from rag_core.errors import CliError, ErrorKind
from rag_core.openrouter import make_embedder
from rag_core.store import open_collection
from tests.conftest import fake_embed


def status_error(status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://x/api/v1/embeddings")
    response = httpx.Response(status, request=request)
    return openai.APIStatusError("boom", response=response, body=None)


def failing_client(status: int) -> SimpleNamespace:
    def create(**_):
        raise status_error(status)

    return SimpleNamespace(
        base_url=httpx.URL("https://x/api/v1/"),
        api_key="k",
        embeddings=SimpleNamespace(create=create),
    )


def key_info(monkeypatch, daily):
    response = httpx.Response(200, json={"data": {"free_model_daily_requests": daily}})
    monkeypatch.setattr(openrouter.httpx, "get", lambda *a, **k: response)


def test_embedder_prepends_prefix_to_every_text():
    sent = []

    def create(model, input):
        sent.append(input)
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.0]) for i in range(len(input))]
        )

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))

    make_embedder(client, "m", "passage: ")(["a", "b"])

    assert sent == [["passage: a", "passage: b"]]


@pytest.mark.parametrize(
    ("status", "daily", "kind"),
    [
        (429, {"used": 50, "limit": 50, "remaining": 0}, ErrorKind.QUOTA),
        (429, {"used": 3, "limit": 50, "remaining": 47}, ErrorKind.RATE_LIMIT),
        (402, None, ErrorKind.QUOTA),
        (401, None, ErrorKind.CONFIG),
        (503, None, ErrorKind.API),
    ],
)
def test_embedder_maps_api_failures_to_error_kinds(monkeypatch, status, daily, kind):
    if daily:
        key_info(monkeypatch, daily)

    with pytest.raises(CliError) as caught:
        make_embedder(failing_client(status), "m")(["a"])

    assert caught.value.kind is kind


def test_embedder_reports_rate_limit_when_quota_lookup_fails(monkeypatch):
    def broken(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(openrouter.httpx, "get", broken)

    with pytest.raises(CliError) as caught:
        make_embedder(failing_client(429), "m")(["a"])

    assert caught.value.kind is ErrorKind.RATE_LIMIT


def test_ingestion_exits_with_quota_code_and_keeps_old_index(
    monkeypatch, settings, docs_dir, capsys
):
    ingest(build_chunks(docs_dir, 40, 10), settings, fake_embed, corpus_hash(docs_dir))
    before = open_collection(settings).get()["ids"]
    key_info(monkeypatch, {"used": 50, "limit": 50, "remaining": 0})
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("CHROMA_DIR", str(settings.chroma_dir))
    monkeypatch.setattr("sys.argv", ["ingestion", "--docs", str(docs_dir)])
    monkeypatch.setattr("ingestion.__main__.make_client", lambda s: failing_client(429))

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 7
    assert "[ERROR QUOTA]" in capsys.readouterr().err
    assert open_collection(settings).get()["ids"] == before
