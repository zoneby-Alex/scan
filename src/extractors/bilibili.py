import asyncio
import json
import re

import httpx

from src.extractors.base import BaseExtractor
from src.models import SubtitleEntry, VideoMeta

_BILI_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?bilibili\.com/video/(BV[\w]+)"
)

_BILI_API_INFO = "https://api.bilibili.com/x/web-interface/view"
_BILI_API_PLAYER = "https://api.bilibili.com/x/player/v2"
_BILI_API_PLAYER_WBI = "https://api.bilibili.com/x/player/wbi/v2"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


class BilibiliExtractor(BaseExtractor):
    platform = "bilibili"

    def match(self, url: str) -> bool:
        return bool(_BILI_URL_PATTERN.search(url))

    def extract(self, url: str) -> VideoMeta:
        bvid = self._parse_bvid(url)
        info = self._fetch_info(bvid)
        cid = info.get("cid", 0)
        return VideoMeta(
            platform="bilibili",
            video_id=bvid,
            title=info.get("title", ""),
            url=url,
            duration=int(info.get("duration", 0) or 0),
            author=info.get("owner", {}).get("name", ""),
            thumbnail=info.get("pic", ""),
            subtitles=self._fetch_subtitles(bvid, cid),
        )

    def _parse_bvid(self, url: str) -> str:
        m = _BILI_URL_PATTERN.search(url)
        if not m:
            raise ValueError(f"无法解析 Bilibili URL: {url}")
        return m.group(1)

    def _fetch_info(self, bvid: str) -> dict:
        r = httpx.get(_BILI_API_INFO, params={"bvid": bvid}, headers=_HEADERS)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"B站 API 错误 (code={data.get('code')}): {data.get('message', 'unknown')}")
        return data["data"]

    def _fetch_subtitles(self, bvid: str, cid: int) -> list[SubtitleEntry]:
        # Try multiple endpoints to find subtitles
        for endpoint in (_BILI_API_PLAYER, _BILI_API_PLAYER_WBI):
            subs = self._try_fetch_subs(endpoint, bvid, cid)
            if subs:
                return subs
        raise RuntimeError(
            "该视频没有可用字幕（B站大部分视频不支持CC字幕，"
            "字幕通常是内嵌在视频画面中的硬字幕，需要通过Whisper语音识别提取）"
        )

    def _try_fetch_subs(self, endpoint: str, bvid: str, cid: int) -> list[SubtitleEntry]:
        try:
            r = httpx.get(endpoint, params={"bvid": bvid, "cid": cid}, headers=_HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
            subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
        except Exception:
            return []

        if not subtitles:
            return []

        # Prefer Chinese
        sub = next(
            (s for s in subtitles if "zh" in s.get("lang", "").lower().replace("-", "")),
            subtitles[0],
        )
        sub_url = sub.get("subtitle_url", "")
        if not sub_url:
            return []

        # Bilibili subtitle URLs might be relative
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        elif sub_url.startswith("/"):
            sub_url = "https://i0.hdslb.com" + sub_url

        sub_r = httpx.get(sub_url, headers=_HEADERS, timeout=15)
        sub_r.raise_for_status()
        entries = sub_r.json().get("body", [])
        return [
            SubtitleEntry(start=float(e["from"]), end=float(e["to"]), text=e["content"])
            for e in entries
        ]
