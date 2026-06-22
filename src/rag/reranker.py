"""RAG reranker using bge-reranker-v2-m3 CrossEncoder for two-stage retrieval."""

from sentence_transformers import CrossEncoder

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder("BAAI/bge-reranker-v2-m3")
    return _reranker


def rerank(query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
    """Rerank candidates by cross-encoder relevance score."""
    if len(candidates) <= top_n:
        return candidates

    model = _get_reranker()
    pairs = [(query, c["text"]) for c in candidates]
    scores = model.predict(pairs)
    scored = [(c, float(s)) for c, s in zip(candidates, scores)]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:top_n]]
