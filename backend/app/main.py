import logging
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import load_settings
from .db import Database
from .llm import AllProvidersUnavailable, Gateway, parse_simulation
from .schemas import ChatRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


def create_app() -> FastAPI:
    settings = load_settings()
    db = Database(settings.db_path)
    gateway = Gateway(settings)

    app = FastAPI(title="LLM Chatbot with Key Failover and Response Caching")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(_: Request, exc: RequestValidationError):
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/health")
    def health():
        try:
            db.ping()
        except Exception:
            raise HTTPException(status_code=503, detail="database unavailable")
        if not gateway.keys:
            raise HTTPException(status_code=503, detail="no API keys configured")
        return {
            "status": "ok",
            "keys_configured": len(gateway.keys),
            "cache_enabled": True,
        }

    @app.post("/chat")
    def chat(req: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None)):
        started = time.perf_counter()
        simulate = {}
        if settings.enable_failure_simulation:
            simulate = parse_simulation(x_simulate_failure, gateway.key_ids)

        override = (req.system_prompt or "").strip() or None
        system_prompt = override or db.get_system_prompt(req.session_id) or settings.default_system_prompt
        history = [{"role": m["role"], "content": m["content"]} for m in db.get_history(req.session_id)]
        messages = (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": req.message}]
        )

        try:
            result = gateway.complete(messages, simulate)
        except AllProvidersUnavailable:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        metadata = {
            "resolution_source": result.resolution_source,
            "model": settings.model,
            "latency_ms": max(1, int((time.perf_counter() - started) * 1000)),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        db.save_turn(req.session_id, req.message, result.reply, metadata, override)
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def session_messages(session_id: str):
        messages = db.get_history(session_id)
        if not messages:
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
