import litellm
import pytest
from fastapi.testclient import TestClient

REAL_COMPLETION = litellm.completion


class FakeProvider:
    """Wraps litellm.completion with mock_response so LiteLLM's real cache is exercised.
    Keys listed in `down` behave like a rate-limited provider."""

    def __init__(self):
        self.down: set[str] = set()
        self.calls: list[str] = []  # api_keys that actually reached the "provider"

    def __call__(self, **kw):
        key = kw["api_key"]
        if key in self.down:
            raise litellm.RateLimitError(
                message="429 quota exhausted", llm_provider="gemini", model=kw["model"]
            )
        last = kw["messages"][-1]["content"]
        history_len = len(kw["messages"])
        kw["mock_response"] = f"echo:{last}|turns:{history_len}"
        self.calls.append(key)
        return REAL_COMPLETION(**kw)


@pytest.fixture
def provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(litellm, "completion", p)
    return p


@pytest.fixture
def client(monkeypatch, tmp_path, provider):
    monkeypatch.setenv("LLM_API_KEYS", "k1,k2")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)
