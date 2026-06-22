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


settings = Settings()
