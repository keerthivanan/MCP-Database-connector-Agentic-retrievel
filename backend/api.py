"""
FastAPI backend for the React web application.

Thin HTTP layer over the same agent + MCP server + guardrails used by every
other demo. The browser talks to this API; this API talks to the agent; the
agent talks MCP. Credentials still live only in the MCP server's environment.

Run (from the project root):
    uvicorn backend.api:api --port 8000
    # then open http://localhost:8000

Endpoints:
    GET  /api/meta        -> which DB backend + LLM provider are active
    POST /api/ask         -> {question, history} -> {answer, trace, iterations}
    GET  /api/tables      -> full table browser data (fetched via MCP)
    GET  /api/audit?n=15  -> last n entries of the server-side audit log
    POST /api/guard_demo  -> raw DELETE straight to MCP (guardrail proof)
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.factory import make_agent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
AUDIT_LOG = os.path.join(ROOT, "mcp_server", "audit.log.jsonl")

api = FastAPI(title="Company DB Assistant API")


class AskRequest(BaseModel):
    question: str
    history: list[dict] = []


def web_ask_user(question: str) -> str:
    """The agent can't pause mid-run for terminal input in a web app: tell it
    to end the turn and put its clarifying question in the final answer —
    the user replies in the chat and history carries the context forward."""
    return (
        "(The user cannot be reached mid-query in this interface. End your "
        "turn now and ask the user your clarifying question as your final "
        "answer; they will reply in the chat.)"
    )


@api.get("/api/meta")
def meta():
    # Mirror agent.factory.make_agent's selection exactly: AGENT_PROVIDER wins,
    # else auto-detect from whichever key is present — so the UI banner always
    # reflects the provider the agent actually uses.
    provider = os.environ.get("AGENT_PROVIDER", "").lower()
    if not provider:
        if os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
    if provider == "openai":
        name, model = "OpenAI", os.environ.get("OPENAI_AGENT_MODEL", "gpt-4o-mini")
    elif provider == "anthropic":
        name, model = "Anthropic", os.environ.get("AGENT_MODEL", "claude-opus-4-8")
    else:
        name, model = "none", "-"
    return {
        "db_backend": "PostgreSQL" if os.environ.get("COMPANY_DB_DSN") else "SQLite",
        "provider": name,
        "model": model,
    }


@api.post("/api/ask")
async def ask(req: AskRequest):
    agent = make_agent(ask_user_handler=web_ask_user, verbose=False)
    result = await agent.ask(req.question, history=req.history)
    return {
        "answer": result.answer,
        "iterations": result.iterations,
        "trace": result.tool_calls,
    }


@api.get("/api/tables")
async def tables():
    """Full database browser data — fetched THROUGH MCP (list_tables ->
    describe_schema -> run_query per table), not via a direct DB connection,
    so even the browse view respects the connector boundary and shows up in
    the audit log."""
    from agent.agent import build_server_params
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    def text_of(res):
        return json.loads("".join(c.text for c in res.content if c.type == "text"))

    out = []
    async with stdio_client(build_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = text_of(await session.call_tool("list_tables", {}))["tables"]
            for name in names:
                schema = text_of(await session.call_tool("describe_schema", {"table_name": name}))
                data = text_of(await session.call_tool(
                    "run_query", {"sql": f"SELECT * FROM {name} ORDER BY 1", "limit": 100}
                ))
                out.append({
                    "name": name,
                    "columns": schema.get("columns", []),
                    "foreign_keys": schema.get("foreign_keys", []),
                    "rows": data.get("rows", []),
                    "row_count": data.get("row_count", 0),
                })
    return {"tables": out}


@api.post("/api/guard_demo")
async def guard_demo():
    """Deterministic guardrail proof: sends a raw DELETE straight to the MCP
    server's run_query tool, BYPASSING the LLM entirely. Shows that read-only
    is enforced in server code — even a hostile client (or a jailbroken
    model) calling the tool directly gets rejected."""
    from agent.agent import build_server_params
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    attempted_sql = "DELETE FROM issues WHERE id = 3"
    async with stdio_client(build_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("run_query", {"sql": attempted_sql})
            text = "".join(c.text for c in res.content if c.type == "text")
    return {"attempted_sql": attempted_sql, "server_response": json.loads(text)}


@api.get("/api/audit")
def audit(n: int = 15):
    if not os.path.exists(AUDIT_LOG):
        return {"entries": []}
    with open(AUDIT_LOG, encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    return {"entries": [json.loads(line) for line in reversed(lines)]}


# --- React frontend (built with Vite: frontend/ -> frontend/dist) -----------
DIST = os.path.join(ROOT, "frontend", "dist")

@api.get("/")
def index():
    if not os.path.exists(os.path.join(DIST, "index.html")):
        return {"error": "Frontend not built. Run: cd frontend && npm install && npm run build"}
    return FileResponse(os.path.join(DIST, "index.html"))

if os.path.isdir(os.path.join(DIST, "assets")):
    api.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")
