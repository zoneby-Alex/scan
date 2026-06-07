import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.cache import cache
from src.output.markdown import _OUTPUT_DIR

router = APIRouter()


def scan_dirs():
    """Return list of output root dirs to scan for history records."""
    old_root = Path(__file__).parent.parent.parent / "output"
    dirs = {}
    for root in (_OUTPUT_DIR, old_root):
        if root.exists():
            dirs[str(root)] = root
    return list(dirs.values())


@router.get("/api/history")
def history():
    items = []
    folders = []  # (mtime, overview_path, folder_path, root_dir)
    for root in scan_dirs():
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            overview = folder / "overview.md"
            if not overview.exists():
                continue
            folders.append((overview.stat().st_mtime, overview, folder, root))

    # Deduplicate by base_name (keep newest by mtime)
    seen = set()
    folders.sort(key=lambda x: x[0], reverse=True)
    for mtime, overview, folder, root in folders:
        if folder.name in seen:
            continue
        seen.add(folder.name)
        text = overview.read_text(encoding="utf-8", errors="replace")
        title = ""
        for line in text.split("\n"):
            if line.startswith("# "):
                title = line[2:].replace(" — 内容概览", "").strip()
                break

        # Read meta.json for created_at (stable timestamp), tags, category
        created_at = mtime  # fallback for records before this fix
        tags = []
        category = ""
        mj = folder / "meta.json"
        if mj.exists():
            try:
                md = json.loads(mj.read_text(encoding="utf-8"))
                created_at = md.get("created_at", mtime)
                tags = md.get("tags", [])
                category = md.get("category", "")
            except Exception:
                pass

        items.append({
            "base_name": folder.name,
            "title": title or folder.name,
            "time": created_at,
            "path": str(root),
            "tags": tags,
            "category": category,
            "files": {
                "subtitles": str((folder / "subtitles.md").relative_to(root)),
                "overview": str((folder / "overview.md").relative_to(root)),
                "keypoints": str((folder / "keypoints.md").relative_to(root)),
            },
        })

    # Sort by created_at so new records are immune to st_mtime drift
    items.sort(key=lambda x: x["time"], reverse=True)
    return items


@router.delete("/api/history/{base_name:path}")
def delete_history(base_name: str):
    import shutil
    for root in scan_dirs():
        resolved_root = root.resolve()
        folder = (resolved_root / base_name).resolve()
        if not str(folder).startswith(str(resolved_root) + os.sep):
            continue
        if folder.is_dir():
            cache.delete(base_name)
            try:
                from src.rag.vectorstore import _client
                _client.delete_collection(base_name)
            except Exception:
                pass
            shutil.rmtree(folder)
            return {"ok": True}
    raise HTTPException(404, "记录不存在")


@router.get("/api/history/{base_name:path}")
def history_detail(base_name: str):
    folder = None
    for root in scan_dirs():
        resolved_root = root.resolve()
        f = (resolved_root / base_name).resolve()
        if not str(f).startswith(str(resolved_root) + os.sep):
            continue
        if f.is_dir():
            folder = f
            break
    if folder is None:
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
        pending = None
        for line in text.split("\n"):
            if line.startswith("| 时间 |"):
                in_table = True
                pending = None
                continue
            if in_table and line.startswith("|---"):
                continue
            if in_table and line.startswith("|") and not line.startswith("| 时间"):
                parts_raw = line.split("|")
                cells = [c.strip() for c in parts_raw[1:]]
                if len(cells) >= 3:
                    ts, content, stars = cells[0], cells[1], cells[2]
                    importance = stars.count("★")
                    timestamp = 0
                    parts_ts = ts.split(":")
                    if len(parts_ts) == 2:
                        timestamp = int(parts_ts[0]) * 60 + int(parts_ts[1])
                    elif len(parts_ts) == 3:
                        timestamp = int(parts_ts[0]) * 3600 + int(parts_ts[1]) * 60 + int(parts_ts[2])
                    result["keypoints"].append({
                        "timestamp": timestamp, "content": content, "importance": importance,
                    })
                    pending = None
                else:
                    pending = cells
            elif in_table and not line.startswith("|"):
                if pending and len(pending) >= 1:
                    tail_parts = line.strip().split("|")
                    if len(tail_parts) >= 2:
                        content = (pending[1].strip() if len(pending) >= 2 else "") + tail_parts[0].strip()
                        stars = tail_parts[1].strip()
                        ts = pending[0]
                        importance = stars.count("★")
                        timestamp = 0
                        parts_ts = ts.split(":")
                        if len(parts_ts) == 2:
                            timestamp = int(parts_ts[0]) * 60 + int(parts_ts[1])
                        elif len(parts_ts) == 3:
                            timestamp = int(parts_ts[0]) * 3600 + int(parts_ts[1]) * 60 + int(parts_ts[2])
                        result["keypoints"].append({
                            "timestamp": timestamp, "content": content, "importance": importance,
                        })
                    pending = None
                else:
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
