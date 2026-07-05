import { useState, useEffect } from "react";

const OUTCOME = {
  ok: ["🟢", "ok"],
  rejected_by_guard: ["🔴", "bad"],
  sql_error: ["🟠", "warn"],
  not_found: ["🟡", "warn"],
};

/**
 * Live view of the MCP server's audit log — refreshed after every answer.
 * The log is written server-side, so the LLM cannot see or tamper with it.
 */
export default function AuditPanel({ refreshKey }) {
  const [entries, setEntries] = useState([]);

  useEffect(() => {
    fetch("/api/audit?n=18")
      .then((r) => r.json())
      .then((d) => setEntries(d.entries))
      .catch(() => {});
  }, [refreshKey]);

  return (
    <aside>
      <h2>Server audit log</h2>
      <p className="note">Written server-side — the LLM cannot see or edit this.</p>
      {entries.map((e, i) => {
        const [mark, cls] = OUTCOME[e.outcome] || ["⚪", ""];
        const arg = (e.args.sql || e.args.table_name || "").slice(0, 64);
        return (
          <div key={i} className="audit-entry">
            {mark} {e.ts.slice(11, 19)} <span className="tool">{e.tool}</span>{" "}
            <span className={cls}>{e.outcome}</span>
            {arg && <div>{arg}</div>}
          </div>
        );
      })}
    </aside>
  );
}
