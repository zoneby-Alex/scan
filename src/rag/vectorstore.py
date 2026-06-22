from chromadb import PersistentClient
from chromadb.utils import embedding_functions

from src.config import PROJECT_ROOT, settings

_client = PersistentClient(path=str(PROJECT_ROOT / ".chromadb"))
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


GLOBAL_COLLECTION = "all_videos"


def index_global(segments: list[tuple[str, str, float, str]]) -> None:
    """Index subtitle segments into the global cross-video collection.
    segments: list of (id, text, timestamp, source_video_name)
    """
    col = _client.get_or_create_collection(
        name=GLOBAL_COLLECTION, embedding_function=_ef
    )
    existing = set(col.get()["ids"])
    new_items = [(id_, text, ts, sv) for id_, text, ts, sv in segments if id_ not in existing]
    if not new_items:
        return
    col.add(
        ids=[x[0] for x in new_items],
        documents=[x[1] for x in new_items],
        metadatas=[{"timestamp": x[2], "source": x[3]} for x in new_items],
    )


def search_global(query: str, k: int = 5, use_rerank: bool | None = None) -> list[dict]:
    """Search across all indexed videos with optional reranking."""
    if use_rerank is None:
        use_rerank = settings.rag_rerank
    col = _client.get_or_create_collection(
        name=GLOBAL_COLLECTION, embedding_function=_ef
    )
    recall_k = k * settings.rag_recall_multiplier if use_rerank else k
    results = col.query(query_texts=[query], n_results=recall_k)
    candidates = []
    for i in range(len(results["ids"][0])):
        candidates.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "timestamp": results["metadatas"][0][i].get("timestamp", 0),
            "source": results["metadatas"][0][i].get("source", ""),
        })
    if use_rerank and len(candidates) > k:
        from src.rag.reranker import rerank
        return rerank(query, candidates, top_n=k)
    return candidates[:k]


def search(collection_name: str, query: str, k: int = 5, use_rerank: bool | None = None) -> list[dict]:
    """Search a single video's subtitles with optional reranking."""
    if use_rerank is None:
        use_rerank = settings.rag_rerank
    col = get_or_create(collection_name)
    recall_k = k * settings.rag_recall_multiplier if use_rerank else k
    results = col.query(query_texts=[query], n_results=recall_k)
    candidates = []
    for i, doc_id in enumerate(results["ids"][0]):
        candidates.append({
            "id": doc_id,
            "text": results["documents"][0][i],
            "timestamp": results["metadatas"][0][i].get("timestamp", 0) if results["metadatas"][0] else 0,
        })
    if use_rerank and len(candidates) > k:
        from src.rag.reranker import rerank
        return rerank(query, candidates, top_n=k)
    return candidates[:k]
