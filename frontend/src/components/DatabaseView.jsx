import { useState, useEffect } from "react";

/**
 * Browsable view of the whole database: every table, its columns (with PK/FK
 * markers), and its actual rows. The data is fetched THROUGH the MCP tools
 * (list_tables -> describe_schema -> run_query), so even this browse view
 * respects the connector boundary — check the audit log after loading it.
 */
export default function DatabaseView() {
  const [tables, setTables] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/tables")
      .then((r) => {
        if (!r.ok) throw new Error(`API returned ${r.status}`);
        return r.json();
      })
      .then((d) => setTables(d.tables))
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="dbview"><p className="note">Failed to load tables: {error}</p></div>;
  if (!tables) return <div className="dbview"><p className="note">Loading tables through MCP…</p></div>;

  return (
    <div className="dbview">
      <p className="note">
        Live contents of every table — fetched through the same 3 MCP tools the
        agent uses (see the audit log fill up when this view loads).
      </p>
      {tables.map((t) => (
        <section key={t.name} className="dbtable">
          <h3>
            <span className="mono">{t.name}</span>
            <span className="count">{t.row_count} rows</span>
          </h3>
          {t.foreign_keys.length > 0 && (
            <p className="fk">
              {t.foreign_keys.map((fk, i) => (
                <span key={i} className="mono">
                  {fk.column} → {fk.references_table}.{fk.references_column}
                  {i < t.foreign_keys.length - 1 ? "  ·  " : ""}
                </span>
              ))}
            </p>
          )}
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  {t.columns.map((c) => (
                    <th key={c.name}>
                      {c.name}
                      {c.primary_key ? " 🔑" : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t.rows.map((row, i) => (
                  <tr key={i}>
                    {t.columns.map((c) => (
                      <td key={c.name}>{row[c.name] === null ? "—" : String(row[c.name])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
