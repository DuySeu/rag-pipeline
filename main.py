from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from core.config import load_settings
from core.constants import model
from core.errors import CliError
from core.openrouter import make_client, make_embedder
from core.store import open_collection

# Exit code per answer kind; 5 to 8 belong to CliError
RESULT_EXIT_CODES = {"answer": 0, "clarify": 3, "refuse": 4}
LOG_FORMAT = "%(levelname)s %(message)s"
# Edit these to try another model; changing EMBED_MODEL_ID means re-running `python main.py ingest`
EMBED_MODEL_ID = model.NEMOTRON_3_EMBED_1B
CHAT_MODEL_ID = model.GEMMA_4_31B_IT

logger = logging.getLogger(__name__)

# Each command imports its pipeline inside the function so `ask` does not pay ~1 s for ragas


# Chunk and embed the docs folder into the vector store
def run_ingest(args: argparse.Namespace) -> None:
    from ingestion.corpus import corpus_hash
    from ingestion.pipeline import build_chunks, ingest

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)

    # Loader errors stop the run here with exit 1; API errors map to codes 5 to 8
    try:
        chunks = build_chunks(
            args.docs, settings.chunk_size, settings.chunk_overlap, settings.chunk_strategy
        )
        logger.info("Built %d chunks from %s", len(chunks), args.docs)

        client = make_client(settings)
        embed = make_embedder(client, settings.embed_model, settings.embed_passage_prefix)
        stored = ingest(chunks, settings, embed, corpus_hash(args.docs))
    except CliError as exc:
        print(f"[ERROR {exc.kind.name}] {exc} (the previous index is unchanged)", file=sys.stderr)
        sys.exit(exc.kind.value)

    logger.info("Stored %d chunks in %s/%s", stored, settings.chroma_dir, settings.collection)


# Answer one question from the vector store, printing a labelled first line
def run_ask(args: argparse.Namespace) -> None:
    from retrieval.pipeline import ask

    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)

    # Expected API and config failures print a labelled line and exit with their own code
    try:
        client = make_client(settings)
        embed = make_embedder(client, settings.embed_model, settings.embed_query_prefix)
        _, answer = ask(args.question, open_collection(settings), embed, client, settings)
    except CliError as exc:
        print(f"[ERROR {exc.kind.name}] {exc}", file=sys.stderr)
        sys.exit(exc.kind.value)

    print(f"[{answer.kind.upper()}]")
    print(answer.text)
    if answer.kind == "answer" and answer.citations:
        print("\nSources:")
        for source, locator in answer.citations:
            print(f"- {source}" + ("" if locator is None else f" ({locator})"))
    sys.exit(RESULT_EXIT_CODES[answer.kind])


# Run the ingestion check, write the report and exit non-zero on failure
def run_eval_ingestion(args: argparse.Namespace) -> None:
    from evaluation.golden import load_golden
    from evaluation.ingestion_eval import REPORT_PATH, evaluate_ingestion

    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)
    golden_sources = [s for sample in load_golden() for s in sample.sources]
    report = evaluate_ingestion(args.docs, settings, open_collection(settings), golden_sources)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    sys.exit(0 if report["passed"] else 1)


# Run the retrieval evaluation and print the summary
def run_eval_retrieval(args: argparse.Namespace) -> None:
    from evaluation.retrieval_eval import run

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)
    try:
        print(json.dumps(asyncio.run(run(settings)), indent=2))
    except CliError as exc:
        # Rows already written to the samples file stay for the next run to compare
        print(f"[ERROR {exc.kind.name}] {exc}", file=sys.stderr)
        sys.exit(exc.kind.value)


# Parse the subcommand and run it
def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest, query and evaluate the RAG pipeline")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_parser = commands.add_parser("ingest", help="Ingest documents into the vector store")
    ingest_parser.add_argument("--docs", type=Path, default=Path("data/docs"))
    ingest_parser.set_defaults(run=run_ingest)

    ask_parser = commands.add_parser("ask", help="Ask a question against the vector store")
    ask_parser.add_argument("question")
    ask_parser.set_defaults(run=run_ask)

    eval_ingestion_parser = commands.add_parser(
        "eval-ingestion", help="Check the vector store against the docs folder"
    )
    eval_ingestion_parser.add_argument("--docs", type=Path, default=Path("data/docs"))
    eval_ingestion_parser.set_defaults(run=run_eval_ingestion)

    eval_retrieval_parser = commands.add_parser(
        "eval-retrieval", help="Score retrieval and answers over the golden set"
    )
    eval_retrieval_parser.set_defaults(run=run_eval_retrieval)

    args = parser.parse_args()

    # Errors a command does not handle itself, such as an empty .env value, still exit 5 to 8
    try:
        args.run(args)
    except CliError as exc:
        print(f"[ERROR {exc.kind.name}] {exc}", file=sys.stderr)
        sys.exit(exc.kind.value)


if __name__ == "__main__":
    main()
