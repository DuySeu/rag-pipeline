from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ingestion.corpus import corpus_hash
from ingestion.pipeline import build_chunks, ingest
from rag_core.errors import CliError
from rag_core.openrouter import make_client, make_embedder
from rag_core.settings import load_settings

logger = logging.getLogger(__name__)


# Chunk and embed the docs folder into the vector store
def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest documents into the vector store")
    parser.add_argument("--docs", type=Path, default=Path("data/docs"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings()

    # Loader errors stop the run here with exit 1; API errors map to codes 5 to 8
    try:
        chunks = build_chunks(args.docs, settings.chunk_size, settings.chunk_overlap)
        logger.info("Built %d chunks from %s", len(chunks), args.docs)

        client = make_client(settings)
        embed = make_embedder(client, settings.embed_model, settings.embed_passage_prefix)
        stored = ingest(chunks, settings, embed, corpus_hash(args.docs))
    except CliError as exc:
        print(f"[ERROR {exc.kind.name}] {exc} (the previous index is unchanged)", file=sys.stderr)
        sys.exit(exc.kind.value)

    logger.info("Stored %d chunks in %s/%s", stored, settings.chroma_dir, settings.collection)


if __name__ == "__main__":
    main()
