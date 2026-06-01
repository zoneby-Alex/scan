from src.llm import chat
from src.rag.vectorstore import index_subtitles, search

_CHAT_SYSTEM = """你是一个视频内容问答助手。根据提供的视频字幕片段回答用户问题。

规则:
1. 仅基于提供的字幕内容回答，不要编造信息
2. 如果字幕中没有相关信息，明确说"视频中未提及"
3. 回答时引用具体的时间戳，方便用户定位
4. 保持回答简洁、准确
5. 用中文回答（除非字幕本身是英文）"""


def build_index(task_id: str, texts: list[str], timestamps: list[float]):
    segments = [
        (f"{task_id}_{i}", text, ts)
        for i, (text, ts) in enumerate(zip(texts, timestamps))
    ]
    index_subtitles(task_id, segments)


def ask(task_id: str, question: str) -> str:
    results = search(task_id, question, k=5)
    if not results:
        return "视频中未提及相关内容。"

    context = "\n\n".join(
        f"[{_fmt_ts(r['timestamp'])}] {r['text']}" for r in results
    )
    prompt = f"字幕片段:\n{context}\n\n用户问题: {question}"
    return chat(_CHAT_SYSTEM, prompt)


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
