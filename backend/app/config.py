"""Environment-driven configuration. Nothing here is ever logged with secrets."""
import os
from dataclasses import dataclass, field

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    api_keys: list[str] = field(default_factory=list, repr=False)
    model: str = "gemini/gemini-2.5-flash"
    database_url: str = "sqlite:////data/app.db"
    allowed_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    enable_failure_simulation: bool = False
    cache_type: str = "local"  # "local" (in-memory) or "disk"
    cache_dir: str = "/data/llm_cache"
    key_cooldown_seconds: int = 30
    request_timeout_seconds: int = 30
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @property
    def db_path(self) -> str:
        url = self.database_url
        if not url.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL must start with sqlite:///")
        return url[len("sqlite:///"):]  # sqlite:////data/app.db -> /data/app.db


def load_settings() -> Settings:
    return Settings(
        api_keys=_split(os.getenv("LLM_API_KEYS", "")),
        model=os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash"),
        database_url=os.getenv("DATABASE_URL", "sqlite:////data/app.db"),
        allowed_origins=_split(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")),
        enable_failure_simulation=os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true",
        cache_type=os.getenv("CACHE_TYPE", "local").lower(),
        cache_dir=os.getenv("CACHE_DIR", "/data/llm_cache"),
        key_cooldown_seconds=int(os.getenv("KEY_COOLDOWN_SECONDS", "30")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
        default_system_prompt=os.getenv("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
    )
