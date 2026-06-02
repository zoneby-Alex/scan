import asyncio
import json
import queue
import re
import threading
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.analyzers import extract_keypoints, summarize
from src.cache import cache
from src.config import settings
from src.extractors import get_extractor
from src.models import AnalysisResult, SubtitleEntry, VideoMeta
from src.output.markdown import generate_all
from src.preprocess.cleaner import clean
from src.preprocess.dedup import deduplicate
from src.preprocess.segmenter import build_text, segment, merge_short
from src.rag.chat import ask as rag_ask, build_index
from src.transcriber import cleanup_audio, download_audio, transcribe

# Shared yt-dlp opts for Bilibili (cookie support)
_BILI_YTDLP_OPTS = {}
if settings.bilibili_cookies:
    cookie_path = Path(settings.bilibili_cookies)
    if cookie_path.exists():
        _BILI_YTDLP_OPTS["cookiefile"] = str(cookie_path.resolve())
    _BILI_YTDLP_OPTS.setdefault("extractor_args", {"bilibili": {"skip_login": ["true"]}})

app = FastAPI(title="Video Analyzer")

_static = Path(__file__).parent / "static"


def _sanitize_task_id(raw: str) -> str:
    """Generate a ChromaDB-safe collection name from a raw URL fragment."""
    import hashlib
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", raw)
    # Strip leading/trailing non-alphanumeric chars (ChromaDB requirement)
    cleaned = cleaned.strip("_.-")
    # Must start and end with alphanumeric
    if not cleaned or len(cleaned) < 3:
        cleaned = "task_" + hashlib.md5(raw.encode()).hexdigest()[:16]
    elif not cleaned[0].isalpha():
        cleaned = "v_" + cleaned
    if not cleaned[-1].isalnum():
        cleaned = cleaned.rstrip("_.-")
    return cleaned[:63]


@app.get("/", response_class=HTMLResponse)
def index():
    return (_static / "index.html").read_text(encoding="utf-8")


SSE_EVENTS: dict[str, queue.Queue] = {}


def _push(task_id: str, event: str, data: str = ""):
    """Thread-safe push to SSE queue."""
    if task_id not in SSE_EVENTS:
        return
    SSE_EVENTS[task_id].put({"event": event, "data": data})


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
    task_id = _sanitize_task_id(url.rsplit("/", 1)[-1][:50])
    SSE_EVENTS[task_id] = queue.Queue()

    async def run():
        audio_path = None
        try:
            _push(task_id, "status", "提取字幕中...")
            extractor = get_extractor(url)
            title = author = thumb = ""
            duration = 0

            try:
                meta = await asyncio.to_thread(extractor.extract, url)
                title = meta.title
                author = meta.author
                duration = meta.duration
                thumb = meta.thumbnail
            except Exception as e:
                _push(task_id, "status", f"API 提取失败 ({e})，改用语音识别...")
                # Get basic info via yt-dlp (may fail for some sites like Bilibili)
                opts = {"quiet": True, "no_warnings": True, "extract_flat": False, **_BILI_YTDLP_OPTS}
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                    title = info.get("title", "") or title
                    author = info.get("uploader", "") or author
                    duration = int(info.get("duration", 0) or 0) or duration
                    thumb = info.get("thumbnail", "") or thumb
                except Exception:
                    pass  # Use whatever we already have from partial extract
                meta = None

            subs = meta.subtitles if meta else []

            # Whisper fallback if no subtitles from platform
            if not subs:
                _push(task_id, "status", "无可用字幕，正在下载音频...")
                audio_path = await asyncio.to_thread(download_audio, url,
                    lambda pct: _push(task_id, "download", f"{pct:.0f}"))

                _push(task_id, "status", "正在语音识别（可能需要几分钟）...")
                subs = await asyncio.to_thread(transcribe, audio_path,
                    lambda pct: _push(task_id, "transcribe", f"{pct:.0f}"))

                _push(task_id, "status", f"语音识别完成 ({len(subs)} 条)")
                # Preserve video_id from already-fetched metadata
                existing_video_id = meta.video_id if meta else ""
                meta = VideoMeta(
                    platform=extractor.platform,
                    video_id=existing_video_id,
                    title=title,
                    url=url,
                    duration=duration,
                    author=author,
                    subtitles=subs,
                    thumbnail=thumb,
                )

            if not subs:
                _push(task_id, "error", "该视频没有可用字幕，语音识别也未获取到内容")
                return

            _push(task_id, "status", f"字幕获取成功 ({len(meta.subtitles)} 条)，预处理中...")

            subs = clean(meta.subtitles)
            subs = deduplicate(subs)
            subs = merge_short(subs)

            _push(task_id, "status", f"预处理完成 ({len(subs)} 条)，AI 分析中...")

            summary_text, chapters = await asyncio.to_thread(
                summarize, subs, meta.title,
                lambda msg: _push(task_id, "status", msg))
            keypoints = await asyncio.to_thread(
                extract_keypoints, subs, meta.title,
                lambda msg: _push(task_id, "status", msg))

            # Translation for non-Chinese videos
            translated = False
            from src.translate import needs_translation, translate_keypoints, translate_subtitles, translate_text
            if needs_translation(subs):
                translated = True
                _push(task_id, "status", "检测到外文内容，正在翻译...")
                keypoints = await asyncio.to_thread(translate_keypoints, keypoints)
                summary_text = summary_text + "\n\n## 中文对照\n" + await asyncio.to_thread(translate_text, summary_text)
                sub_zh = await asyncio.to_thread(translate_subtitles, subs)
                for i, zh in enumerate(sub_zh):
                    if i < len(subs) and zh:
                        subs[i].text = subs[i].text + "\n" + zh

            result = AnalysisResult(
                meta=VideoMeta(
                    platform=meta.platform,
                    video_id=meta.video_id,
                    title=meta.title,
                    url=meta.url,
                    duration=meta.duration,
                    author=meta.author,
                    subtitles=subs,
                    thumbnail=meta.thumbnail,
                ),
                summary=summary_text,
                chapters=chapters,
                keypoints=keypoints,
            )

            base_name = re.sub(r"[\\/:*?\"<>|' ]", "_", meta.title)[:60]
            files = await asyncio.to_thread(generate_all, result, base_name)

            # Persist metadata for history
            from src.output.markdown import _OUTPUT_DIR
            _meta = {
                "title": meta.title, "platform": meta.platform,
                "author": meta.author, "duration": meta.duration,
                "url": meta.url, "thumbnail": meta.thumbnail,
            }
            if meta.subtitles:
                _push(task_id, "status", "分类中...")
                from src.classify import classify
                try:
                    tags = await asyncio.to_thread(classify, meta.title, summary_text)
                    _meta.update(tags)
                except Exception:
                    pass
            (_OUTPUT_DIR / base_name / "meta.json").write_text(
                json.dumps(_meta, ensure_ascii=False, indent=2), encoding="utf-8")

            cache.set(task_id, result)

            # Build RAG index
            _push(task_id, "status", "构建检索索引...")
            segs = segment(subs)
            seg_texts = [build_text(s) for s in segs]
            seg_starts = [s[0].start for s in segs]
            await asyncio.to_thread(build_index, task_id, seg_texts, seg_starts)

            # Build subtitle text preview
            sub_lines = []
            for e in subs[:500]:
                m, s = divmod(int(e.start), 60)
                h, m2 = divmod(m, 60)
                if h:
                    ts = f"{h}:{m2:02d}:{s:02d}"
                else:
                    ts = f"{m}:{s:02d}"
                sub_lines.append(f"[{ts}] {e.text}")

            _push(task_id, "done", json.dumps({
                "title": meta.title,
                "author": meta.author,
                "duration": meta.duration,
                "thumbnail": meta.thumbnail,
                "summary": summary_text[:500],
                "keypoints": [{"timestamp": kp.timestamp, "content": kp.content, "importance": kp.importance} for kp in keypoints],
                "files": {k: str(v) for k, v in files.items()},
                "subtitles": sub_lines,
                "overview": summary_text,
                "translated": translated,
            }))
        except Exception as e:
            _push(task_id, "error", str(e))
        finally:
            if audio_path:
                try:
                    await asyncio.to_thread(cleanup_audio, audio_path)
                except Exception:
                    pass

    threading.Thread(target=asyncio.run, args=(run(),), daemon=True).start()
    return {"task_id": task_id}


@app.get("/api/download/{filepath:path}")
def download(filepath: str):
    from src.output.markdown import _OUTPUT_DIR

    fp = _OUTPUT_DIR / filepath
    if not fp.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(fp, filename=fp.name, media_type="text/markdown")


@app.get("/api/history")
def history():
    from src.output.markdown import _OUTPUT_DIR
    items = []
    folders = []
    for folder in _OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        overview = folder / "overview.md"
        if not overview.exists():
            continue
        folders.append((overview.stat().st_mtime, overview, folder))
    # Sort by mtime descending (newest first)
    folders.sort(key=lambda x: x[0], reverse=True)

    for mtime, overview, folder in folders:
        text = overview.read_text(encoding="utf-8", errors="replace")
        title = ""
        for line in text.split("\n"):
            if line.startswith("# "):
                title = line[2:].replace(" — 内容概览", "").strip()
                break
        # Load tags from meta.json
        tags = []
        category = ""
        mj = folder / "meta.json"
        if mj.exists():
            try:
                md = json.loads(mj.read_text(encoding="utf-8"))
                tags = md.get("tags", [])
                category = md.get("category", "")
            except Exception:
                pass

        items.append({
            "base_name": folder.name,
            "title": title or folder.name,
            "time": mtime,
            "tags": tags,
            "category": category,
            "files": {
                "subtitles": str((folder / "subtitles.md").relative_to(_OUTPUT_DIR)),
                "overview": str((folder / "overview.md").relative_to(_OUTPUT_DIR)),
                "keypoints": str((folder / "keypoints.md").relative_to(_OUTPUT_DIR)),
            },
        })
    return items


@app.delete("/api/history/{base_name:path}")
def delete_history(base_name: str):
    from src.output.markdown import _OUTPUT_DIR
    import shutil
    folder = _OUTPUT_DIR / base_name
    if not folder.is_dir():
        raise HTTPException(404, "记录不存在")
    # Remove from cache and ChromaDB if possible
    cache.delete(base_name)
    try:
        from src.rag.vectorstore import _client
        _client.delete_collection(base_name)
    except Exception:
        pass
    shutil.rmtree(folder)
    return {"ok": True}


@app.get("/api/history/{base_name:path}")
def history_detail(base_name: str):
    from src.output.markdown import _OUTPUT_DIR
    folder = _OUTPUT_DIR / base_name
    if not folder.is_dir():
        raise HTTPException(404, "记录不存在")

    result = {"base_name": base_name, "title": base_name, "subtitles": [], "overview": "", "keypoints": [], "thumbnail": "", "category": "", "tags": []}

    # Load meta.json
    mj = folder / "meta.json"
    if mj.exists():
        try:
            meta = json.loads(mj.read_text(encoding="utf-8"))
            result["title"] = meta.get("title", base_name)
            result["thumbnail"] = meta.get("thumbnail", "")
            result["platform"] = meta.get("platform", "")
            result["author"] = meta.get("author", "")
            result["duration"] = meta.get("duration", "")
            result["url"] = meta.get("url", "")
            result["category"] = meta.get("category", "")
            result["tags"] = meta.get("tags", [])
        except Exception:
            pass

    # Parse overview
    ov = folder / "overview.md"
    if ov.exists():
        text = ov.read_text(encoding="utf-8", errors="replace")
        for line in text.split("\n"):
            if line.startswith("# "):
                result["title"] = line[2:].replace(" — 内容概览", "").strip()
                break
        # Extract source info
        for line in text.split("\n"):
            if line.startswith("**来源**:"):
                parts = line.replace("**来源**: ", "").split("|")
                if len(parts) >= 2:
                    result["platform"] = parts[0].strip()
                    result["author"] = parts[1].replace("**作者**:", "").strip()
                    dur_part = parts[2] if len(parts) > 2 else ""
                    result["duration"] = dur_part.replace("**时长**:", "").strip()
            if line.startswith("**原链**:"):
                result["url"] = line.replace("**原链**: ", "").strip()
        result["overview"] = text

    # Parse keypoints
    kp = folder / "keypoints.md"
    if kp.exists():
        text = kp.read_text(encoding="utf-8", errors="replace")
        in_table = False
        for line in text.split("\n"):
            if line.startswith("| 时间 |"):
                in_table = True
                continue
            if in_table and line.startswith("|---"):
                continue
            if in_table and line.startswith("|") and not line.startswith("| 时间"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 3:
                    ts = cells[0]
                    content = cells[1]
                    stars = cells[2]
                    importance = stars.count("★")
                    timestamp = 0
                    parts = ts.split(":")
                    if len(parts) == 2:
                        timestamp = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        timestamp = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    result["keypoints"].append({
                        "timestamp": timestamp, "content": content, "importance": importance,
                    })
            elif in_table and not line.startswith("|"):
                break

    # Parse subtitles
    sub = folder / "subtitles.md"
    if sub.exists():
        text = sub.read_text(encoding="utf-8", errors="replace")
        import re as _re
        for line in text.split("\n"):
            m = _re.match(r"^\[(\d+):(\d+)(?::(\d+))?\]\s+(.+)", line)
            if m:
                result["subtitles"].append(line.strip())

    return result


@app.get("/api/chat")
async def chat_endpoint(task_id: str = Query(...), q: str = Query(...)):
    try:
        answer = await asyncio.to_thread(rag_ask, task_id, q)
        return {"answer": answer}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


def main():
    import uvicorn

    uvicorn.run("src.web.server:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
