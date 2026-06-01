from src.extractors.base import BaseExtractor
from src.extractors.bilibili import BilibiliExtractor
from src.extractors.youtube import YouTubeExtractor

_extractors: list[BaseExtractor] = [YouTubeExtractor(), BilibiliExtractor()]


def get_extractor(url: str) -> BaseExtractor:
    for ex in _extractors:
        if ex.match(url):
            return ex
    raise ValueError(f"不支持该视频平台: {url}")
