import asyncio
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

import httpx

from src.extractors.base import BaseExtractor, clean_url
from src.models import SubtitleEntry, VideoMeta

_BILI_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?bilibili\.com/video/(BV[\w]+)"
)
_BILI_COLLECTION_URL_PATTERN = re.compile(
    r"(?:https?://)?space\.bilibili\.com/(\d+)/lists/(\d+)"
)

_BILI_API_INFO = "https://api.bilibili.com/x/web-interface/view"
_BILI_API_PLAYER = "https://api.bilibili.com/x/player/v2"
_BILI_API_PLAYER_WBI = "https://api.bilibili.com/x/player/wbi/v2"
_BILI_API_NAV = "https://api.bilibili.com/x/web-interface/nav"
_BILI_API_PLAYURL = "https://api.bilibili.com/x/player/playurl"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

_BASE_HEADERS = {
    "Referer": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.bilibili.com",
}


def _load_cookie_str() -> str:
    """Load Bilibili cookies from Netscape-format file (BILIBILI_COOKIES env var)."""
    cookie_path = os.environ.get("BILIBILI_COOKIES", "")
    if not cookie_path:
        return ""
    try:
        p = Path(cookie_path)
        if not p.exists():
            p = Path.cwd() / cookie_path
        if not p.exists():
            return ""
        cookies = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("HttpOnly "):
                line = line[9:]
            parts = line.split("\t")
            if len(parts) >= 7:
                domain = parts[0]
                name, value = parts[5], parts[6]
                if "bilibili.com" in domain:
                    cookies.append(f"{name}={value}")
        return "; ".join(cookies)
    except Exception:
        return ""


_COOKIE_STR = _load_cookie_str()


def _get_headers(ua_index: int = 0) -> dict:
    h = {
        "User-Agent": _USER_AGENTS[ua_index % len(_USER_AGENTS)],
        **_BASE_HEADERS,
    }
    if _COOKIE_STR:
        h["Cookie"] = _COOKIE_STR
    return h


def _retry_get(url: str, params: dict = None, max_retries: int = 3) -> httpx.Response:
    """GET with UA rotation and exponential backoff on 412."""
    for attempt in range(max_retries):
        headers = _get_headers(attempt)
        r = httpx.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 412 and attempt < max_retries - 1:
            time.sleep(1.5 ** attempt + random.uniform(0, 0.5))
            continue
        r.raise_for_status()
        return r
    # unreachable
    raise httpx.HTTPStatusError("max retries exceeded", request=None, response=r)


def _sign_wbi(params: dict) -> dict:
    """Apply WBI signature to params. Falls back to unsigned params on failure."""
    try:
        r = httpx.get(_BILI_API_NAV, headers=_get_headers(), timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            return params
        img_url = data["data"]["wbi_img"]["img_url"]
        sub_url = data["data"]["wbi_img"]["sub_url"]
        img_key = img_url.rsplit("/", 1)[1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[1].split(".")[0]
        mix_key = sub_key[:4] + img_key[:4]
    except Exception:
        return params

    params = dict(sorted(params.items()))
    params["wts"] = int(time.time())
    query = "&".join(f"{k}={v}" for k, v in params.items())
    params["w_rid"] = hashlib.md5((query + mix_key).encode()).hexdigest()
    return params


def fetch_bilibili_audio_url(bvid: str, cid: int) -> tuple[str, str] | None:
    """Get DASH audio URL from Bilibili playurl API. Returns (url, mime_type) or None."""
    try:
        params = {"bvid": bvid, "cid": cid, "fnval": 4048, "fourk": 1}
        r = _retry_get(_BILI_API_PLAYURL, params=params)
        data = r.json()
        audios = data.get("data", {}).get("dash", {}).get("audio", [])
        if not audios:
            return None
        best = max(audios, key=lambda a: a.get("bandwidth", 0))
        return best["baseUrl"], best.get("mimeType", "audio/mp4")
    except Exception:
        return None


def expand_collection(url: str) -> tuple[list[str], str]:
    """Expand a Bilibili collection URL into (video_urls, author_name).
    Uses yt-dlp's BilibiliCollectionList extractor.
    """
    m = _BILI_COLLECTION_URL_PATTERN.search(url)
    if not m:
        return [], ""

    import yt_dlp as _yt_dlp
    opts = {"quiet": True, "extract_flat": True}
    try:
        with _yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = info.get("entries", [])
        uploader = info.get("uploader", "") or ""
        urls = [
            e.get("url") or f"https://www.bilibili.com/video/{e['id']}"
            for e in entries if e.get("id")
        ]
        return urls, uploader
    except Exception:
        return [], ""


class BilibiliExtractor(BaseExtractor):
    platform = "bilibili"

    def match(self, url: str) -> bool:
        return bool(_BILI_URL_PATTERN.search(url))

    def extract(self, url: str) -> VideoMeta:
        bvid = self._parse_bvid(url)
        info = self._fetch_info(bvid)
        cid = info.get("cid", 0)
        subs = []
        try:
            subs = self._fetch_subtitles(bvid, cid)
        except RuntimeError:
            pass  # 无字幕，server.py 会走 Whisper 回退
        return VideoMeta(
            platform="bilibili",
            video_id=bvid,
            title=info.get("title", ""),
            url=clean_url(url),
            duration=int(info.get("duration", 0) or 0),
            author=info.get("owner", {}).get("name", ""),
            thumbnail=info.get("pic", ""),
            subtitles=subs,
        )

    def _parse_bvid(self, url: str) -> str:
        m = _BILI_URL_PATTERN.search(url)
        if not m:
            raise ValueError(f"无法解析 Bilibili URL: {url}")
        return m.group(1)

    def _fetch_info(self, bvid: str) -> dict:
        r = _retry_get(_BILI_API_INFO, params={"bvid": bvid})
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"B站 API 错误 (code={data.get('code')}): {data.get('message', 'unknown')}"
            )
        return data["data"]

    def _fetch_subtitles(self, bvid: str, cid: int) -> list[SubtitleEntry]:
        # Try player/v2 first (simple, no WBI)
        subs = self._try_fetch_subs(_BILI_API_PLAYER, bvid, cid)
        if subs:
            return subs

        # Try player/wbi/v2 with WBI signature
        params = {"bvid": bvid, "cid": cid}
        signed = _sign_wbi(params)
        if "w_rid" in signed:
            subs = self._try_fetch_subs(_BILI_API_PLAYER_WBI, None, None, params=signed)
            if subs:
                return subs

        raise RuntimeError(
            "该视频没有可用字幕（B站大部分视频不支持CC字幕，"
            "字幕通常是内嵌在视频画面中的硬字幕，需要通过Whisper语音识别提取）"
        )

    def _try_fetch_subs(
        self, endpoint: str, bvid: str, cid: int, params: dict = None
    ) -> list[SubtitleEntry]:
        try:
            if params is None:
                params = {"bvid": bvid, "cid": cid}
            r = _retry_get(endpoint, params=params)
            data = r.json()
            subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        except Exception:
            return []

        if not subtitles:
            return []

        sub = next(
            (s for s in subtitles if "zh" in s.get("lang", "").lower().replace("-", "")),
            subtitles[0],
        )
        sub_url = sub.get("subtitle_url", "")
        if not sub_url:
            return []

        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        elif sub_url.startswith("/"):
            sub_url = "https://i0.hdslb.com" + sub_url

        try:
            sub_r = _retry_get(sub_url)
            entries = sub_r.json().get("body", [])
        except Exception:
            return []

        return [
            SubtitleEntry(start=float(e["from"]), end=float(e["to"]), text=e["content"])
            for e in entries
        ]
