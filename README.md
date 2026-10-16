# RAG pipeline

Two pipelines that run on their own and share only the Chroma store on disk (`data/chroma`): ingestion writes it, retrieval reads it. Embeddings and the chat model go through OpenRouter.

## Layout

| Path | What it holds |
| --- | --- |
| `data/docs/` | Input documents (`.md`, `.txt`) |
| `ingestion/` | Load, chunk, embed, write to Chroma (`python -m ingestion`) |
| `retrieval/` | Embed the question, query Chroma, answer with the chat model (`python -m retrieval`) |
| `rag_core/` | Settings, OpenRouter client and embedder, Chroma collection access |
| `evaluation/datasets/golden.jsonl` | Golden questions with reference answer and source file |
| `evaluation/ingestion_eval.py` | Checks the store against the docs folder, no API calls |
| `evaluation/retrieval_eval.py` | hit rate@k, MRR and RAGAS metrics over the golden set |
| `evaluation/reports/` | Reports written by the two evals |

## Run

```sh
cp .env.example .env   # then fill in OPENROUTER_API_KEY
uv sync
uv run --env-file .env python -m ingestion
uv run --env-file .env python -m retrieval "Mỗi tuần được làm việc từ xa tối đa mấy ngày?"
uv run --env-file .env python -m evaluation.ingestion_eval
uv run --env-file .env python -m evaluation.retrieval_eval
uv run pytest
```

Ingestion rebuilds the whole collection on each run. Re-run it after changing documents, `EMBED_MODEL`, `CHUNK_SIZE` or `CHUNK_OVERLAP`; retrieval must use the same `EMBED_MODEL` that built the store.

## Golden set format

One JSON object per line. `sources` lists file paths relative to `data/docs/` that contain the answer.

```json
{"question": "...", "reference": "...", "sources": ["nghi-phep.md"]}
```

## Retrieval metrics

| Metric | Needs LLM | Measures |
| --- | --- | --- |
| `hit_rate@k` | no | Share of questions where an expected source is in the top k |
| `mrr` | no | Mean of 1/rank of the first expected source |
| `context_precision` | yes | Whether relevant chunks rank above irrelevant ones (vs reference) |
| `context_recall` | yes | How much of the reference answer the retrieved chunks support |
| `faithfulness` | yes | Share of answer claims backed by the retrieved chunks |
| `answer_relevancy` | yes | How well the answer addresses the question |
