from pathlib import Path

from diskcache import Cache

_cache_dir = Path(__file__).parent.parent / ".cache"
_cache_dir.mkdir(exist_ok=True)

cache = Cache(str(_cache_dir))
