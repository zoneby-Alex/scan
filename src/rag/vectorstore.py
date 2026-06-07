from chromadb import PersistentClient
from chromadb.utils import embedding_functions

_client = PersistentClient(path="./.chromadb")
_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-m3"
)


def get_or_create(collection_name: str):
    return _client.get_or_create_collection(
        name=collection_name,
        embedding_function=_ef,
    )


def index_subtitles(collection_name: str, segments: list[tuple[str, str, float]]):
    """segments: list of (id, text, timestamp)"""
    col = get_or_create(collection_name)
    existing = set(col.get()["ids"])
    new_items = [(id_, text, ts) for id_, text, ts in segments if id_ not in existing]
    if not new_items:
        return
    col.add(
        ids=[x[0] for x in new_items],
        documents=[x[1] for x in new_items],
        metadatas=[{"timestamp": x[2]} for x in new_items],
    )


def search(collection_name: str, query: str, k: int = 5) -> list[dict]:
    col = get_or_create(collection_name)
    results = col.query(query_texts=[query], n_results=k)
    out = []
    for i, doc_id in enumerate(results["ids"][0]):
        out.append({
            "id": doc_id,
            "text": results["documents"][0][i],
            "timestamp": results["metadatas"][0][i].get("timestamp", 0) if results["metadatas"][0] else 0,
        })
    return out
