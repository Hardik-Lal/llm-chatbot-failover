"""LiteLLM gateway: key pool with automatic failover + built-in response cache."""
import logging
import threading
import time
from dataclasses import dataclass

import litellm

from .config import Settings

log = logging.getLogger("gateway")
VALID_SIM_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    pass


@dataclass
class _Key:
    id: str
    secret: str
    cooldown_until: float = 0.0


@dataclass
class LLMResult:
    reply: str
    resolution_source: str
    key_id: str | None
    fallback_attempts: int
    input_tokens: int
    output_tokens: int


def parse_simulation(header: str | None, key_ids: list[str]) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {key_id: status}. Bad pairs are ignored."""
    sim: dict[str, int] = {}
    if not header:
        return sim
    for pair in header.split(","):
        if "=" not in pair:
            continue
        name, _, status = pair.partition("=")
        name, status = name.strip(), status.strip()
        if not status.isdigit() or int(status) not in VALID_SIM_STATUSES:
            continue
        targets = key_ids if name == "*" else ([name] if name in key_ids else [])
        for t in targets:
            sim[t] = int(status)
    return sim


class Gateway:
    def __init__(self, settings: Settings):
        self.model = settings.model
        self.cooldown = settings.key_cooldown_seconds
        self.timeout = settings.request_timeout_seconds
        self.keys = [_Key(f"key_{i}", k) for i, k in enumerate(settings.api_keys, start=1)]
        self._lock = threading.Lock()
        # LiteLLM's built-in cache. Cache key = model + full messages list (api_key is not part of it).
        if settings.cache_type == "disk":
            litellm.cache = litellm.Cache(type="disk", disk_cache_dir=settings.cache_dir)
        else:
            litellm.cache = litellm.Cache(type="local")
        litellm.drop_params = True

    @property
    def key_ids(self) -> list[str]:
        return [k.id for k in self.keys]

    def _ordered_keys(self) -> list[_Key]:
        """key_1 first; keys that recently failed for real are tried last."""
        now = time.time()
        with self._lock:
            healthy = [k for k in self.keys if k.cooldown_until <= now]
            cooling = [k for k in self.keys if k.cooldown_until > now]
        return healthy + cooling

    def _mark_failed(self, key: _Key):
        with self._lock:
            key.cooldown_until = time.time() + self.cooldown

    def complete(self, messages: list[dict], simulate: dict[str, int] | None = None) -> LLMResult:
        simulate = simulate or {}
        attempts = 0
        for key in self._ordered_keys():
            if key.id in simulate:
                log.warning("simulated %s failure for %s", simulate[key.id], key.id)
                attempts += 1
                continue
            try:
                resp = litellm.completion(
                    model=self.model,
                    messages=messages,
                    api_key=key.secret,
                    timeout=self.timeout,
                    num_retries=0,  # we do our own key-level failover
                    # A request using the failure-simulation header bypasses the cache so the
                    # failover demo always shows a live answer from the surviving key.
                    caching=not simulate,
                )
                reply = (resp.choices[0].message.content or "").strip()
                if not reply:
                    raise ValueError("empty completion")
            except Exception as exc:  # 429, quota, 5xx, timeouts, auth... -> next key
                attempts += 1
                self._mark_failed(key)
                log.warning("%s failed (%s): %s", key.id, type(exc).__name__, str(exc)[:160])
                continue

            hit = bool((getattr(resp, "_hidden_params", None) or {}).get("cache_hit"))
            usage = getattr(resp, "usage", None)
            return LLMResult(
                reply=reply,
                resolution_source="LLM_CACHE" if hit else "LIVE_INFERENCE",
                key_id=None if hit else key.id,
                fallback_attempts=0 if hit else attempts,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            )
        raise AllProvidersUnavailable()
