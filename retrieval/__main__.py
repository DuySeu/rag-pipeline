from __future__ import annotations

import argparse
import sys

from rag_core.errors import CliError
from rag_core.openrouter import make_client, make_embedder
from rag_core.settings import load_settings
from rag_core.store import open_collection
from retrieval.pipeline import ask

# Exit code per result kind; 5 to 8 belong to CliError
RESULT_EXIT_CODES = {"answer": 0, "clarify": 3, "refuse": 4}


# Answer one question from the vector store, printing a labelled first line
def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question against the vector store")
    parser.add_argument("question")
    args = parser.parse_args()

    settings = load_settings()

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


if __name__ == "__main__":
    main()
