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
from core.store import delete_collection, open_collection

# Exit code per answer kind; 5 to 8 belong to CliError
RESULT_EXIT_CODES = {"answer": 0, "clarify": 3, "refuse": 4}
LOG_FORMAT = "%(levelname)s %(message)s"
# Edit these to try another model; changing EMBED_MODEL_ID means re-running `python main.py ingest`
EMBED_MODEL_ID = model.NEMOTRON_3_EMBED_1B
CHAT_MODEL_ID = model.GEMMA_4_31B_IT

logger = logging.getLogger(__name__)

# Each command imports its pipeline inside the function so `ask` does not pay ~1 s for ragas


# Reject a blank question before any API call
def non_blank(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("question must not be blank")
    return value


# Chunk and embed the docs folder into the vector store
def run_ingest(args: argparse.Namespace) -> None:
    from ingestion.pipeline import ingest

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)
    docs_dir = args.docs or settings.docs_dir

    # Loader errors stop the run here with exit 1; API errors map to codes 5 to 8
    try:
        client = make_client(settings)
        embed = make_embedder(client, settings.embed_model)
        result = ingest(docs_dir, settings, embed)
    except CliError as exc:
        print(f"[ERROR {exc.kind.name}] {exc} (the previous index is unchanged)", file=sys.stderr)
        sys.exit(exc.kind.value)

    plan = result.plan
    if plan is None:
        logger.info("Rebuilt store: embedded %d chunks", result.embedded_chunks)
    elif result.embedded_chunks or plan.changed or plan.removed:
        logger.info(
            "new=%d changed=%d removed=%d unchanged=%d, embedded %d chunks",
            len(plan.new), len(plan.changed), len(plan.removed), len(plan.unchanged),
            result.embedded_chunks,
        )
    else:
        logger.info("Store is up to date, nothing to embed")
    if result.empty_files:
        logger.warning("Files with no text to chunk: %s", ", ".join(result.empty_files))
    logger.info(
        "Stored %d chunks in %s/%s", result.stored_chunks, settings.chroma_dir, settings.collection
    )


# Drop the vector store collection; `ingest` builds it again
def run_delete_collection(args: argparse.Namespace) -> None:
    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)
    target = f"collection {settings.collection} in {settings.chroma_dir}"
    print(f"Deleted {target}" if delete_collection(settings) else f"No {target}, nothing deleted")


# Answer one question from the vector store, printing a labelled first line
def run_ask(args: argparse.Namespace) -> None:
    from retrieval.pipeline import ask

    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)

    # Expected API and config failures print a labelled line and exit with their own code
    try:
        client = make_client(settings)
        embed = make_embedder(client, settings.embed_model)
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
    from evaluation.ingestion_eval import REPORT_NAME, evaluate_ingestion

    settings = load_settings(EMBED_MODEL_ID, CHAT_MODEL_ID)
    golden_sources = [s for sample in load_golden(settings.golden_path) for s in sample.sources]
    report = evaluate_ingestion(args.docs or settings.docs_dir, settings, open_collection(settings), golden_sources)

    report_path = settings.reports_dir / REPORT_NAME
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
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
    ingest_parser.add_argument("--docs", type=Path, help="default: ingestion.docs_dir")
    ingest_parser.set_defaults(run=run_ingest)

    delete_parser = commands.add_parser(
        "delete-collection", help="Delete the vector store collection named in config.yaml"
    )
    delete_parser.set_defaults(run=run_delete_collection)

    ask_parser = commands.add_parser("ask", help="Ask a question against the vector store")
    ask_parser.add_argument("question", type=non_blank)
    ask_parser.set_defaults(run=run_ask)

    eval_ingestion_parser = commands.add_parser(
        "eval-ingestion", help="Check the vector store against the docs folder"
    )
    eval_ingestion_parser.add_argument("--docs", type=Path, help="default: ingestion.docs_dir")
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
