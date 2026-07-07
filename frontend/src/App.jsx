import { useState, useEffect, useRef, useCallback } from "react";
import MessageBubble from "./components/MessageBubble.jsx";
import AuditPanel from "./components/AuditPanel.jsx";
import DatabaseView from "./components/DatabaseView.jsx";

const SAMPLES = [
  "Fetch employee details where department = 'AI'",
  "Which AI-team members have open issues on Project Phoenix?",
  "Who has the most open critical issues?",
];

export default function App() {
  const [meta, setMeta] = useState({ db_backend: "…", provider: "…", model: "" });
  const [view, setView] = useState("chat"); // "chat" | "database"
  const [chat, setChat] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [auditKey, setAuditKey] = useState(0);
  const bottomRef = useRef(null);
  // Stable reference so passing it to DatabaseView's effect doesn't re-run it.
  const bumpAudit = useCallback(() => setAuditKey((k) => k + 1), []);

  useEffect(() => {
    fetch("/api/meta").then((r) => r.json()).then(setMeta).catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat, busy]);

  const guardTest = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setChat((c) => [
      ...c,
      {
        role: "user",
        content:
          "🛡️ Guardrail test — send a raw DELETE straight to the MCP server, bypassing the LLM",
      },
    ]);
    try {
      const res = await fetch("/api/guard_demo", { method: "POST" });
      if (!res.ok) throw new Error(`API returned ${res.status}`);
      const data = await res.json();
      setChat((c) => [
        ...c,
        {
          role: "assistant",
          content:
            `Attempted (no LLM involved):\n  ${data.attempted_sql}\n\n` +
            `Server response:\n  rejected_by: ${data.server_response.rejected_by}\n` +
            `  ${data.server_response.error}\n\n` +
            `This is enforcement in server code — even a hostile client calling ` +
            `the tool directly gets refused. See the 🔴 entry in the audit log →`,
        },
      ]);
    } catch (err) {
      setChat((c) => [
        ...c,
        { role: "assistant", content: `Guard demo failed: ${err.message}` },
      ]);
    } finally {
      setBusy(false);
      setAuditKey((k) => k + 1);
    }
  }, [busy]);

  const send = useCallback(
    async (text) => {
      const question = (text ?? input).trim();
      if (!question || busy) return;
      setInput("");
      setBusy(true);
      const history = chat.map((m) => ({ role: m.role, content: m.content }));
      setChat((c) => [...c, { role: "user", content: question }]);
      try {
        const res = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, history }),
        });
        if (!res.ok) throw new Error(`API returned ${res.status}`);
        const data = await res.json();
        setChat((c) => [
          ...c,
          { role: "assistant", content: data.answer, trace: data.trace },
        ]);
      } catch (err) {
        setChat((c) => [
          ...c,
          {
            role: "assistant",
            content: `Something went wrong talking to the backend: ${err.message}`,
          },
        ]);
      } finally {
        setBusy(false);
        setAuditKey((k) => k + 1);
      }
    },
    [input, busy, chat]
  );

  return (
    <>
      <header>
        <h1>🔌 Company DB Assistant</h1>
        <span className="badge">{meta.db_backend}</span>
        <span className="badge">
          {meta.provider}
          {meta.model ? ` · ${meta.model}` : ""}
        </span>
        <nav className="tabs">
          <button
            className={view === "chat" ? "tab active" : "tab"}
            onClick={() => setView("chat")}
          >
            💬 Chat
          </button>
          <button
            className={view === "database" ? "tab active" : "tab"}
            onClick={() => setView("database")}
          >
            🗄️ Database
          </button>
        </nav>
        <div className="spacer" />
        <span className="badge">MCP · read-only · audited</span>
      </header>

      <main>
        {view === "database" && <DatabaseView onLoaded={bumpAudit} />}
        <div className="chatcol" style={{ display: view === "chat" ? "flex" : "none" }}>
          <div className="messages">
            {chat.length === 0 && (
              <div className="msg assistant">
                <div className="bubble">
                  Ask me anything about the company's employees, projects and
                  issues. I can only reach the database through 3 guarded MCP
                  tools — try asking me to delete something and watch the guard
                  say no.
                </div>
              </div>
            )}
            {chat.map((m, i) => (
              <MessageBubble key={i} msg={m} />
            ))}
            {busy && (
              <div className="msg assistant">
                <div className="bubble thinking">
                  <span /><span /><span />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="samples">
            {SAMPLES.map((s) => (
              <button key={s} className="chip" onClick={() => send(s)} disabled={busy}>
                {s}
              </button>
            ))}
            <button className="chip danger" onClick={guardTest} disabled={busy}>
              🛡️ Guardrail test: raw DELETE (bypasses the LLM)
            </button>
          </div>

          <div className="inputbar">
            <input
              value={input}
              placeholder="e.g. Which AI-team members have open issues on Project Phoenix?"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              disabled={busy}
            />
            <button onClick={() => send()} disabled={busy || !input.trim()}>
              Ask
            </button>
          </div>
        </div>

        <AuditPanel refreshKey={auditKey} />
      </main>
    </>
  );
}
