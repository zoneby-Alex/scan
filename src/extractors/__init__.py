from src.extractors.base import BaseExtractor
from src.extractors.bilibili import BilibiliExtractor, expand_collection, fetch_collection_author
from src.extractors.youtube import YouTubeExtractor, extract_playlist_urls

_extractors: list[BaseExtractor] = [YouTubeExtractor(), BilibiliExtractor()]


def get_extractor(url: str) -> BaseExtractor:
    for ex in _extractors:
        if ex.match(url):
            return ex
    raise ValueError(f"不支持该视频平台: {url}")


def expand_playlist(url: str) -> tuple[list[str], str]:
    """Expand a playlist/collection URL into individual video URLs + author name.
    Returns (urls, author). Supports YouTube playlists and Bilibili collections.
    """
    # Try YouTube playlist
    urls = extract_playlist_urls(url)
    if urls:
        return urls, ""

    # Try Bilibili collection
    urls = expand_collection(url)
    if urls:
        author = fetch_collection_author(url)
        return urls, author

    return [], ""
