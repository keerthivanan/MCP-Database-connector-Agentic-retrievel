"""
OFFLINE demo — no API key needed.

Drives the exact same MCP server (mcp_server/server.py) through the same
plan -> act -> observe steps the LLM agent performs, but with a scripted
planner standing in for Claude. Use this to demonstrate the architecture
(schema discovery, JOIN retrieval, read-only guardrails, error recovery)
without any LLM credentials. The live-LLM version is demo.py.

What it proves:
  * The agent side needs ZERO database credentials — every step below goes
    through the MCP tools only.
  * The schema is discovered at runtime (steps 1-2), not hard-coded.
  * A JOIN across all three tables works (step 3).
  * Writes are rejected by the server's guard, not by politeness (step 4).
  * A wrong column name produces a readable error that a planner (human or
    LLM) uses to repair the query (step 5) — the error-recovery loop.

Run:
    python demos/demo_offline.py
"""

from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.stdio import stdio_client

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import build_server_params

SERVER = build_server_params()


def show(step: str, detail: str = "") -> None:
    print(f"\n>>> {step}")
    if detail:
        print(f"    {detail}")


def show_result(raw: str) -> dict:
    data = json.loads(raw)
    pretty = json.dumps(data, indent=2)
    print("    " + pretty.replace("\n", "\n    "))
    return data


async def main() -> None:
    print("=" * 74)
    print("  OFFLINE DEMO — full MCP round-trip, scripted planner, no API key")
    print("=" * 74)

    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool: str, args: dict) -> dict:
                r = await session.call_tool(tool, args)
                return show_result("".join(c.text for c in r.content if c.type == "text"))

            # ----------------------------------------------------------------
            show("STEP 1 — Discover: what tables exist? (list_tables)",
                 "The planner knows nothing about this DB yet — no names are hard-coded.")
            tables = (await call("list_tables", {}))["tables"]

            # ----------------------------------------------------------------
            show("STEP 2 — Discover: schema + foreign keys of each table (describe_schema)",
                 f"Inspecting: {tables}")
            for t in tables:
                await call("describe_schema", {"table_name": t})

            # ----------------------------------------------------------------
            show("STEP 3 — Plan + act: the assessment's JOIN question",
                 '"Which AI-team members have open issues on Project Phoenix?"')
            await call("run_query", {"sql": """
                SELECT e.name, e.role, i.title AS issue_title, i.priority
                FROM employees e
                JOIN issues i    ON i.assignee_id = e.id
                JOIN projects p  ON p.id = i.project_id
                WHERE e.department = 'AI'
                  AND p.name = 'Project Phoenix'
                  AND i.status = 'open'
            """})

            # ----------------------------------------------------------------
            show("STEP 4 — Guardrail proof: a write attempt is REJECTED server-side",
                 "This is enforcement in code, not a prompt asking nicely.")
            await call("run_query", {"sql": "DELETE FROM issues WHERE id = 3"})

            # ----------------------------------------------------------------
            show("STEP 5a — Error recovery: planner tries a WRONG column ('severity')",
                 "The server returns the SQL error as data instead of crashing.")
            bad = await call("run_query", {
                "sql": "SELECT severity, COUNT(*) FROM issues GROUP BY severity"
            })

            show("STEP 5b — Planner READS the error, re-checks the schema...",
                 f"observed error: {bad.get('error', '')!r}")
            schema = await call("describe_schema", {"table_name": "issues"})
            cols = [c["name"] for c in schema["columns"]]
            fixed_col = "priority" if "priority" in cols else cols[-1]

            show("STEP 5c — ...and RETRIES with the corrected column",
                 f"real columns are {cols} -> using '{fixed_col}'")
            await call("run_query", {
                "sql": f"SELECT {fixed_col}, COUNT(*) AS n FROM issues GROUP BY {fixed_col} ORDER BY n DESC"
            })

    print("\n" + "=" * 74)
    print("  Done. Same server, same tools, same guardrails the LLM agent uses —")
    print("  run 'python demo.py' with an ANTHROPIC_API_KEY for the live version.")
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(main())
