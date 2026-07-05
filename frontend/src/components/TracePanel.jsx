const ICONS = {
  list_tables: "🔍",
  describe_schema: "📋",
  run_query: "⚡",
  ask_user: "💬",
};

/**
 * Renders the agent's plan→act→observe trace for one answer: every MCP tool
 * call it made, in order, with errors (and their recovery) highlighted.
 */
export default function TracePanel({ trace }) {
  const errors = trace.filter((t) => t.is_error).length;
  return (
    <details className="trace" open={errors > 0}>
      <summary>
        agent trace — {trace.length} tool calls
        {errors ? ` · ${errors} error(s) recovered` : ""}
      </summary>
      <div className="steps">
        {trace.map((t, i) => {
          const arg = t.input.sql || t.input.table_name || t.input.question || "";
          return (
            <div key={i} className={"step" + (t.is_error ? " err" : "")}>
              {t.is_error ? "❌" : ICONS[t.tool] || "🔧"} <b>{t.tool}</b>{" "}
              {String(arg).slice(0, 120)}
              {t.is_error && (
                <div className="errnote">↳ {t.result_preview.slice(0, 130)}</div>
              )}
            </div>
          );
        })}
      </div>
    </details>
  );
}
