from __future__ import annotations

import argparse

from rag_core.openrouter import make_client, make_embedder
from rag_core.settings import load_settings
from rag_core.store import open_collection
from retrieval.pipeline import generate_answer, retrieve


# Answer one question from the vector store and show the sources used
def main() -> None:
    parser = argparse.ArgumentParser(description="Ask a question against the vector store")
    parser.add_argument("question")
    args = parser.parse_args()

    settings = load_settings()
    client = make_client(settings)

    chunks = retrieve(
        args.question,
        open_collection(settings),
        make_embedder(client, settings.embed_model),
        settings.top_k,
    )
    answer = generate_answer(args.question, chunks, client, settings.chat_model)

    print(answer)
    print("\nSources:")
    for chunk in chunks:
        print(f"- {chunk.source} (distance {chunk.distance:.3f})")


if __name__ == "__main__":
    main()
