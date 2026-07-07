"""
MCP server: exposes the company database to an LLM as three tools.

    list_tables()            -> names of all tables
    describe_schema(table)   -> columns, types, primary/foreign keys for one table
    run_query(sql, limit)    -> executes a READ-ONLY SELECT and returns rows

Works against either engine — chosen by the SERVER's environment, invisible
to the LLM (see backends.py):

    COMPANY_DB_DSN set   -> PostgreSQL, connecting as the SELECT-only
                            `mcp_readonly` role (see db/init_db_postgres.py)
    otherwise            -> SQLite file opened read-only (mode=ro)

The key security properties demonstrated here:

  * Credentials/connection info live ONLY in this server process's
    environment. They are never part of the MCP protocol, never sent to the
    LLM, and never appear in the agent's code or prompts.

  * Every query passes through sql_guard.validate_and_limit() (read-only
    allow-list + hard row limit) BEFORE it reaches the database.

  * The database engine itself enforces read-only independently (SQLite
    mode=ro / Postgres SELECT-only role + read-only session) — even if the
    guard had a bug, the engine would refuse a write.

  * Every tool call is appended to a server-side audit log the LLM cannot
    see or tamper with (audit.log.jsonl).

Run standalone (the agent normally launches it as a subprocess over stdio):

    python mcp_server/server.py
"""

from __future__ import annotations

import datetime
import json
import os
import sys

# Allow "python mcp_server/server.py" to find sibling modules when run as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from sql_guard import validate_and_limit, DEFAULT_LIMIT
from backends import get_backend

backend = get_backend()
mcp = FastMCP("company-database")

# Server-side audit trail: every tool call the LLM makes is appended here
# as one JSON line (timestamp, tool, arguments, outcome). Because this is
# written by the SERVER, the LLM cannot suppress or tamper with it — a
# complete record of everything the AI ever did to the database.
AUDIT_LOG_PATH = os.environ.get(
    "MCP_AUDIT_LOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.log.jsonl"),
)


def _audit(tool: str, args: dict, outcome: str) -> None:
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "backend": backend.name,
        "tool": tool,
        "args": args,
        "outcome": outcome,  # 'ok' | 'rejected_by_guard' | 'sql_error' | 'not_found'
    }
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # auditing must never take the service down


@mcp.tool()
def list_tables() -> str:
    """List all tables available in the company database. Call this first to
    discover what data exists before writing any query."""
    tables = backend.list_tables()
    _audit("list_tables", {}, "ok")
    return json.dumps({"tables": tables})


@mcp.tool()
def describe_schema(table_name: str) -> str:
    """Describe one table: its columns (name, type, nullable, primary key)
    and its foreign-key relationships to other tables. Call this for every
    table you plan to reference in a query, so column names are never guessed."""
    schema = backend.describe_schema(table_name)
    if schema is None:
        _audit("describe_schema", {"table_name": table_name}, "not_found")
        return json.dumps({
            "error": f"Table '{table_name}' does not exist. "
                     f"Use list_tables to see available tables."
        })
    _audit("describe_schema", {"table_name": table_name}, "ok")
    return json.dumps(schema)


@mcp.tool()
def run_query(sql: str, limit: int = DEFAULT_LIMIT) -> str:
    """Execute a READ-ONLY SQL SELECT against the company database and return
    the rows as JSON. Only single SELECT (or WITH...SELECT) statements are
    allowed — INSERT/UPDATE/DELETE/DROP etc. are rejected. Row output is
    capped (default 100, hard max 500). If the query fails, the error message
    is returned so you can correct the query and retry."""
    guard = validate_and_limit(sql, requested_limit=limit)
    if not guard.ok:
        _audit("run_query", {"sql": sql}, "rejected_by_guard")
        return json.dumps({"error": guard.reason, "rejected_by": "sql_guard"})

    try:
        cols, rows = backend.execute(guard.sql)
        _audit("run_query", {"sql": sql}, "ok")
        return json.dumps({
            "columns": cols,
            "row_count": len(rows),
            "rows": rows,
            "row_limit_applied": guard.applied_limit,  # the cap actually applied
        })
    except backend.error_type as exc:
        # Returned as data, not raised: the agent reads this message and
        # self-corrects its SQL (the error-recovery loop).
        _audit("run_query", {"sql": sql}, "sql_error")
        return json.dumps({"error": f"SQL error: {exc}", "rejected_by": backend.name})


if __name__ == "__main__":
    # stdio transport: the agent launches this file as a subprocess and
    # talks JSON-RPC over stdin/stdout. No network port, no exposed DSN.
    mcp.run(transport="stdio")
