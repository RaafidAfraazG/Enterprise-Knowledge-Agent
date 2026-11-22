"""Load markdown into configured Qdrant; in-memory data lasts only for this process."""

import argparse

from kagent.config import Settings
from kagent.llm.factory import get_embedder
from kagent.retrieval.ingest import ingest_docs, make_client

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default="data/docs")
    args = parser.parse_args()
    settings = Settings()
    client = make_client(settings)
    try:
        count = ingest_docs(args.docs, get_embedder(settings), client, settings.qdrant_collection)
        print(f"Ingested {count} chunks")
        if not settings.qdrant_url:
            print("In-memory collection is ephemeral; API startup ingests its own instance.")
    finally:
        client.close()
