import { useEffect, useRef, useState } from "react";

const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

// A new session starts on every page load.
const newSessionId = () =>
  "s-" + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10));

function errorText(status, body) {
  if (status === 503) return "All AI providers are temporarily unavailable. Please try again in a moment.";
  if (status === 422) {
    const d = body && body.detail;
    if (Array.isArray(d) && d[0]) return `Invalid request: ${d[0].msg || "please check your message"}`;
    return "Invalid request: your message must be 1–4000 characters.";
  }
  if (body && typeof body.detail === "string") return body.detail;
  return `Something went wrong (HTTP ${status}).`;
}

function Meta({ m }) {
  return (
    <div className="meta">
      <span className={`badge ${m.resolution_source === "LLM_CACHE" ? "cache" : "live"}`}>
        <span data-testid="meta-source">{m.resolution_source}</span>
      </span>
      <span>model: <b data-testid="meta-model">{m.model}</b></span>
      <span>latency: <b data-testid="meta-latency">{m.latency_ms} ms</b></span>
      <span>in tokens: <b data-testid="meta-input-tokens">{m.input_tokens}</b></span>
      <span>out tokens: <b data-testid="meta-output-tokens">{m.output_tokens}</b></span>
      <span className="muted">key: {m.key_id ?? "—"} · fallbacks: {m.fallback_attempts}</span>
    </div>
  );
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [loading, setLoading] = useState(false);
  const bottom = useRef(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setLoading(true);
    try {
      const body = { session_id: sessionId, message: text };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();
      const headers = { "Content-Type": "application/json" };
      if (simulate) headers["X-Simulate-Failure"] = simulate;
      const res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(body) });
      let data = null;
      try { data = await res.json(); } catch { /* non-JSON body */ }
      if (!res.ok) {
        setMessages((m) => [...m, { role: "error", content: errorText(res.status, data) }]);
      } else {
        setMessages((m) => [...m, { role: "assistant", content: data.reply, metadata: data.metadata }]);
      }
    } catch {
      setMessages((m) => [...m, { role: "error", content: "Cannot reach the server. Check your connection and try again." }]);
    } finally {
      setLoading(false);
    }
  }

  function onKey(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header>
        <h1>LLM Chat</h1>
        <span className="muted">session {sessionId}</span>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>
          System prompt (optional)
          <textarea
            data-testid="system-prompt-input"
            rows={2}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="e.g. You are a pirate. Answer in pirate speak."
          />
        </label>
        <label>
          Simulate failure (demo only)
          <select value={simulate} onChange={(e) => setSimulate(e.target.value)}>
            <option value="">None</option>
            <option value="key_1=429">key_1 → 429 (fail over to key_2)</option>
            <option value="key_1=500">key_1 → 500 (fail over to key_2)</option>
            <option value="*=429">All keys → 429 (clean 503)</option>
          </select>
        </label>
      </details>

      <main className="chat">
        {messages.length === 0 && <p className="empty">Say something to start the conversation.</p>}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="bubble user" data-testid="user-message">{m.content}</div>
          ) : m.role === "error" ? (
            <div key={i} className="bubble error" role="alert" data-testid="error-message">{m.content}</div>
          ) : (
            <div key={i} className="bubble assistant" data-testid="assistant-message">
              <div className="reply">{m.content}</div>
              <Meta m={m.metadata} />
            </div>
          )
        )}
        {loading && <div className="bubble assistant typing">Thinking…</div>}
        <div ref={bottom} />
      </main>

      <footer>
        <textarea
          data-testid="chat-input"
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Type a message…  (Enter to send, Shift+Enter for a new line)"
        />
        <button data-testid="send-button" onClick={send} disabled={loading}>Send</button>
      </footer>
    </div>
  );
}
