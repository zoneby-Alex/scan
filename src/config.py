from pathlib import Path

from pydantic_settings import BaseSettings


PROJECT_ROOT = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    anthropic_auth_token: str = ""
    anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    anthropic_model: str = "deepseek-v4-flash[1m]"
    anthropic_reasoning_model: str = "deepseek-v4-pro"

    host: str = "127.0.0.1"
    port: int = 8787

    bilibili_cookies: str = ""
    ytdlp_cookies: str = ""  # Netscape-format cookie file for yt-dlp
    ytdlp_browser: str = ""  # Browser to extract cookies from, e.g. chrome, edge, firefox
    output_dir: str = ""
    obsidian_vault: str = ""
    whisper_model: str = ""
    pdf_backend: str = "auto"  # "auto" | "weasyprint" | "chrome"

    rag_rerank: bool = True  # enable cross-encoder reranking for RAG
    rag_recall_multiplier: int = 4  # how many extra candidates to fetch for reranker pool

    @property
    def anthropic_kwargs(self) -> dict:
        return {
            "api_key": self.anthropic_auth_token,
            "base_url": self.anthropic_base_url,
        }


def build_ytdlp_options(extra: dict | None = None) -> dict:
    """Build common yt-dlp options, applying configured cookies/browser if available."""
    opts = {"quiet": True, "no_warnings": True}
    if settings.ytdlp_cookies:
        cookie_path = Path(settings.ytdlp_cookies)
        if cookie_path.exists():
            opts["cookiefile"] = str(cookie_path.resolve())
    if settings.ytdlp_browser:
        # yt-dlp Python API expects a tuple/list, not a bare string
        parts = settings.ytdlp_browser.split(":", 3)
        opts["cookiesfrombrowser"] = tuple(parts + [None] * (4 - len(parts)))
    if extra:
        # Deep-merge extractor_args so platform-specific args can coexist
        extra_args = extra.get("extractor_args", {})
        if extra_args:
            opts.setdefault("extractor_args", {})
            for key, val in extra_args.items():
                opts["extractor_args"][key] = val
        for key, val in extra.items():
            if key != "extractor_args":
                opts[key] = val
    return opts


settings = Settings()
