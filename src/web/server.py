import asyncio
import os
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from src.cache import cache
from src.config import settings
from src.output.markdown import _OUTPUT_DIR
from src.rag.chat import ask as rag_ask
from src.web.history import router as history_router, scan_dirs
from src.web.pipeline import SSE_EVENTS, push, run_pipeline, sanitize_task_id

app = FastAPI(title="Video Analyzer")

_static = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
def index():
    return (_static / "index.html").read_text(encoding="utf-8")


@app.get("/api/progress/{task_id}")
async def progress(task_id: str):
    if task_id not in SSE_EVENTS:
        SSE_EVENTS[task_id] = queue.Queue()

    async def gen():
        try:
            while True:
                try:
                    msg = await asyncio.to_thread(SSE_EVENTS[task_id].get, timeout=900)
                    yield f"event: {msg['event']}\ndata: {msg['data']}\n\n"
                    if msg["event"] == "done" or msg["event"] == "error":
                        break
                except queue.Empty:
                    break
        finally:
            SSE_EVENTS.pop(task_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/analyze")
async def analyze(url: str = Query(...)):
    task_id = sanitize_task_id(url.rsplit("/", 1)[-1][:50])
    SSE_EVENTS[task_id] = queue.Queue()
    threading.Thread(target=asyncio.run, args=(run_pipeline(url, task_id),), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/download/{filepath:path}")
def download(filepath: str):
    for root in scan_dirs():
        resolved_root = root.resolve()
        fp = (resolved_root / filepath).resolve()
        if not (str(fp).startswith(str(resolved_root) + os.sep) or fp == resolved_root):
            continue
        if fp.exists():
            return FileResponse(fp, filename=fp.name, media_type="text/markdown")
    raise HTTPException(404, "文件不存在")


@app.get("/api/chat")
async def chat_endpoint(task_id: str = Query(...), q: str = Query(...)):
    try:
        answer = await asyncio.to_thread(rag_ask, task_id, q)
        return {"answer": answer}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


# Include history routes (GET/DELETE /api/history, GET /api/history/{base_name})
app.include_router(history_router)


def main():
    import uvicorn
    uvicorn.run("src.web.server:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
