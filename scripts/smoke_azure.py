"""Opt-in real-provider smoke test; never run this from the offline test suite."""

from uuid import uuid4

from openai import APIConnectionError, AuthenticationError, NotFoundError, RateLimitError
from qdrant_client import models

from kagent.config import Settings
from kagent.llm.factory import get_chat_model, get_embedder
from kagent.retrieval.ingest import make_client


def advice(error: Exception, component: str) -> str:
    """Map failures to useful guidance without printing provider response secrets."""
    if isinstance(error, NotFoundError):
        field = (
            "AZURE_OPENAI_CHAT_DEPLOYMENT"
            if component == "chat"
            else "AZURE_OPENAI_EMBED_DEPLOYMENT"
        )
        return f"Deployment not found: check {field} and AZURE_OPENAI_API_VERSION."
    if isinstance(error, AuthenticationError):
        return "Authentication failed: check AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT."
    if isinstance(error, RateLimitError):
        return "Quota/rate limit reached: check deployment quota and retry later."
    if isinstance(error, APIConnectionError):
        return "Connection failed: check endpoint spelling, firewall, and outbound HTTPS access."
    if component == "qdrant":
        return "Check QDRANT_URL, QDRANT_API_KEY, cluster status, and write permission (401 = key)."
    return "Check Azure endpoint, deployment name, API version, and model compatibility."


def main() -> int:
    """Call chat and embeddings once, then round-trip one isolated Qdrant point."""
    settings = Settings(llm_provider="azure")
    failed = False
    vector: list[float] | None = None
    try:
        answer = get_chat_model(settings).invoke("Reply briefly.", "Say hello.")
        if not answer.strip():
            raise ValueError("Empty chat response")
        print("PASS chat")
    except Exception as error:
        failed = True
        print("FAIL chat: " + advice(error, "chat"))
    try:
        vector = get_embedder(settings).embed(["Azure Qdrant smoke test"])[0]
        if not vector:
            raise ValueError("Empty embedding")
        print("PASS embeddings")
    except Exception as error:
        failed = True
        print("FAIL embeddings: " + advice(error, "embeddings"))
    if vector is None:
        print("FAIL qdrant: embeddings unavailable; fix embeddings and rerun.")
        return 1
    if not settings.qdrant_url:
        print("FAIL qdrant: set QDRANT_URL to a real cluster endpoint.")
        return 1
    client = None
    created = False
    collection = "eval_smoke_" + uuid4().hex
    try:
        client = make_client(settings)
        client.create_collection(
            collection,
            vectors_config=models.VectorParams(size=len(vector), distance=models.Distance.COSINE),
        )
        created = True
        client.upsert(
            collection,
            points=[models.PointStruct(id=1, vector=vector, payload={"smoke": True})],
            wait=True,
        )
        hits = client.query_points(collection, query=vector, limit=1).points
        if not hits or hits[0].id != 1:
            raise ValueError("Round-trip result mismatch")
        print("PASS qdrant")
    except Exception as error:
        failed = True
        print("FAIL qdrant: " + advice(error, "qdrant"))
    finally:
        if client is not None:
            try:
                if created:
                    client.delete_collection(collection)
            except Exception:
                failed = True
                print("FAIL qdrant cleanup: remove temporary collection " + collection)
            finally:
                client.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
