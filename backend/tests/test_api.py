import sqlite3


def chat(client, sid, msg, headers=None, **extra):
    return client.post("/chat", json={"session_id": sid, "message": msg, **extra}, headers=headers or {})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_chat_live_then_cache_hit_in_new_session(client):
    a = chat(client, "s-1", "What is the capital of France?")
    assert a.status_code == 200
    m = a.json()["metadata"]
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert m["key_id"] == "key_1" and m["fallback_attempts"] == 0
    assert m["input_tokens"] > 0 and m["output_tokens"] > 0

    b = chat(client, "s-2", "What is the capital of France?")  # same first message, new session
    mb = b.json()["metadata"]
    assert mb["resolution_source"] == "LLM_CACHE"
    assert b.json()["reply"] == a.json()["reply"]
    assert mb["key_id"] is None and mb["fallback_attempts"] == 0
    assert mb["input_tokens"] == m["input_tokens"] and mb["output_tokens"] == m["output_tokens"]
    assert isinstance(mb["latency_ms"], int)


def test_different_message_never_cached(client):
    chat(client, "s-1", "alpha")
    r = chat(client, "s-2", "beta")
    assert r.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_multi_turn_memory_and_isolation(client, provider):
    chat(client, "s-1", "first")
    r = chat(client, "s-1", "second")
    assert "turns:4" in r.json()["reply"]  # system + user + assistant + new user
    other = chat(client, "s-other", "third")
    assert "turns:2" in other.json()["reply"]

    hist = client.get("/sessions/s-1/messages").json()
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant", "user", "assistant"]
    assert hist["messages"][1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert "system" not in [m["role"] for m in hist["messages"]]


def test_tokens_are_per_request_not_cumulative(client):
    first = chat(client, "s-1", "abcd").json()["metadata"]["input_tokens"]
    again = chat(client, "s-2", "wxyz").json()["metadata"]["input_tokens"]
    assert first == again


def test_failover_on_simulated_429_and_500(client):
    r = chat(client, "s-1", "hello one", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    assert r.json()["metadata"]["fallback_attempts"] == 1
    r = chat(client, "s-2", "hello two", headers={"X-Simulate-Failure": "key_1=500"})
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_failing_is_503_and_not_stored(client):
    for h in ("*=429", "key_1=429,key_2=500"):
        r = chat(client, "s-x", "will fail", headers={"X-Simulate-Failure": h})
        assert r.status_code == 503
        assert r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-x/messages").status_code == 404


def test_real_provider_failure_fails_over(client, provider):
    provider.down.add("k1")
    r = chat(client, "s-1", "real failure")
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    provider.down.add("k2")
    r = chat(client, "s-2", "everything down")
    assert r.status_code == 503


def test_simulation_disabled_ignores_header(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    from fastapi.testclient import TestClient
    from app.main import create_app

    c = TestClient(create_app(), raise_server_exceptions=False)
    r = chat(c, "s-1", "no sim", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 200


def test_validation_errors(client):
    assert client.post("/chat", json={"session_id": "s", "message": "   "}).status_code == 422
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "s"}).status_code == 422
    assert chat(client, "s", "x" * 4001).status_code == 422
    assert chat(client, "s" * 129, "hi").status_code == 422
    bad = client.post("/chat", content="{not json", headers={"content-type": "application/json"})
    assert bad.status_code == 422 and "detail" in bad.json()
    assert client.get("/sessions/s/messages").status_code == 404


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


def test_system_prompt_override_persists_for_session(client):
    r = chat(client, "s-1", "hi", system_prompt="You are a pirate.")
    assert r.status_code == 200
    # later turns reuse the stored prompt: same messages as a fresh session with that prompt -> cache
    r2 = chat(client, "s-2", "hi", system_prompt="You are a pirate.")
    assert r2.json()["metadata"]["resolution_source"] == "LLM_CACHE"


def test_history_survives_in_sqlite(client, tmp_path):
    chat(client, "s-db", "persist me")
    conn = sqlite3.connect(f"{tmp_path}/test.db")
    rows = conn.execute("SELECT role, content FROM messages WHERE session_id='s-db' ORDER BY id").fetchall()
    assert rows[0] == ("user", "persist me") and rows[1][0] == "assistant"


def test_cors_preflight_allows_simulate_header(client):
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
