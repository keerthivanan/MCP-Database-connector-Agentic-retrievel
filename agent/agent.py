"""
Agentic workflow: answers natural-language questions about the company
database by planning and executing queries THROUGH the MCP server.

The loop (plan -> act -> observe):

    1. The agent connects to the MCP server (launched as a subprocess over
       stdio) and asks it what tools it offers. The three DB tools
       (list_tables, describe_schema, run_query) are converted into
       Claude tool definitions automatically — nothing is hard-coded.
    2. Claude is prompted to ALWAYS discover the schema first (list_tables,
       then describe_schema) before writing SQL. Table/column names are
       never baked into this file or the prompt.
    3. Claude plans a SQL SELECT and calls run_query.
    4. If run_query returns an error (bad column, syntax error, guard
       rejection), that error text is fed back to Claude as the tool result.
       Claude reads it and retries with a corrected query — this is the
       error-recovery loop. A max-iteration cap stops runaway loops.
    5. If the question is ambiguous, Claude calls the local ask_user tool
       to get a clarification from the human before querying.
    6. When Claude stops calling tools, its final text is the grounded
       natural-language answer.

Security note: this file contains NO database credentials, NO database
driver import, and NO SQL of its own. Its only secret is the Anthropic API
key (read from the environment). The database is reachable exclusively via
the MCP tools.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client, get_default_environment

# Load the project-root .env so every entry point (demo, backend, chat, app,
# or running this file directly) picks up keys/config without manual exports.
# Real environment variables always take precedence over .env values.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def build_server_params(command: str | None = None, args: list[str] | None = None) -> StdioServerParameters:
    """Launch parameters for the MCP server subprocess.

    The MCP SDK starts servers with a minimal environment by design, so the
    HOST must explicitly forward the server's own configuration (DB location,
    audit log path) — the same way an MCP host passes an `env` block to a
    server subprocess. These values go straight into the server process; the
    LLM never sees them.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = get_default_environment()
    for var in ("COMPANY_DB_DSN", "COMPANY_DB_PATH", "MCP_AUDIT_LOG"):
        if os.environ.get(var):
            env[var] = os.environ[var]
    return StdioServerParameters(
        command=command or sys.executable,
        args=args or [os.path.join(project_root, "mcp_server", "server.py")],
        env=env,
    )

MODEL = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
MAX_ITERATIONS = 12  # hard cap on plan->act->observe cycles per question

SYSTEM_PROMPT = """\
You are a careful data analyst agent. You answer questions about a company
database that you can ONLY access through the tools provided (list_tables,
describe_schema, run_query). You have no other knowledge of this database.

Rules you must follow:
1. DISCOVER FIRST: before writing any SQL, call list_tables, then call
   describe_schema for every table you intend to use. Never guess table or
   column names.
2. READ-ONLY: only SELECT queries. The server will reject anything else.
3. RECOVER FROM ERRORS: if run_query returns an error, read the error
   message carefully, fix the query, and try again. Do not give up after
   one failure, and do not repeat the identical failing query.
4. VERIFY FILTER VALUES: text values are case-sensitive. Before filtering
   on a categorical column (status, department, priority, project name),
   check what values actually exist (e.g. SELECT DISTINCT status FROM ...).
   If a filter unexpectedly returns 0 rows, you MUST check the distinct
   values and retry with the correct value before concluding the answer is
   empty.
5. CLARIFY WHEN AMBIGUOUS: if the question is missing information you need
   to write a correct query — e.g. it refers to a project by a vague name
   ("the migration") or an ambiguous term ("unresolved": open only, or also
   in_progress?) — first look up the candidate values in the data, and if
   more than one interpretation remains, use the ask_user tool to ask ONE
   short, specific clarifying question rather than guessing.
6. GROUND YOUR ANSWER: your final answer must be based only on rows the
   database actually returned. Quote the real names/values from the results.
   If the result set is empty (after verifying your filter values), say so
   plainly.

When you are done, give a clear, concise natural-language answer.
"""

# Local (non-MCP) tool that lets the model ask the human a clarifying question.
ASK_USER_TOOL = {
    "name": "ask_user",
    "description": (
        "Ask the human user ONE short clarifying question when the request is "
        "ambiguous or is missing information needed to write a correct query. "
        "Use sparingly — only when you genuinely cannot proceed safely."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The clarifying question to show the user.",
            }
        },
        "required": ["question"],
    },
}


def _result_is_error(mcp_result, result_text: str) -> bool:
    """True if a tool call failed, so the agent treats it as an observation to
    recover from. Catches BOTH an MCP-level failure (`isError` — e.g. an
    unhandled server exception like a missing database, rendered as plain text
    with no JSON) AND our tools' own structured `{"error": ...}` result — while
    NOT false-positiving on a successful result that merely contains the
    substring "error" somewhere (e.g. a column value)."""
    if getattr(mcp_result, "isError", False):
        return True
    try:
        parsed = json.loads(result_text)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed


def _terminal_ask_user(question: str) -> str:
    """Default clarification handler: ask on the terminal; if there is no
    interactive terminal (EOF), instruct the agent to proceed on its own."""
    try:
        return input(f"\n[agent asks] {question}\n> ")
    except EOFError:
        return (
            "(no human is available right now — proceed with your best "
            "judgment using the actual schema and data values you can inspect)"
        )


@dataclass
class AgentResult:
    answer: str
    iterations: int
    tool_calls: list[dict] = field(default_factory=list)  # trace for the demo


class DatabaseAgent:
    """Connects Claude to the MCP database server and runs the agent loop."""

    def __init__(
        self,
        server_command: str | None = None,
        server_args: list[str] | None = None,
        ask_user_handler: Callable[[str], str] | None = None,
        verbose: bool = True,
    ):
        self.server_params = build_server_params(server_command, server_args)
        self.client = anthropic.Anthropic()
        # Default clarification handler: prompt on the terminal. If no
        # terminal is attached (non-interactive run), tell the agent to
        # proceed with its best schema-grounded judgment instead of crashing.
        self.ask_user_handler = ask_user_handler or _terminal_ask_user
        self.verbose = verbose

    def _log(self, label: str, detail: str = "") -> None:
        if self.verbose:
            print(f"  [{label}] {detail}")

    async def ask(self, question: str, history: list[dict] | None = None) -> AgentResult:
        """Run one full plan->act->observe loop for a natural-language question.

        `history` is an optional list of prior {"role", "content"} text turns
        (e.g. from a chat UI) so follow-up questions keep their context."""
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # --- Tool discovery: MCP tools -> Claude tool definitions.
                # The agent adapts to whatever the server exposes.
                mcp_tools = (await session.list_tools()).tools
                claude_tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": t.inputSchema,
                    }
                    for t in mcp_tools
                ] + [ASK_USER_TOOL]
                self._log("connected", f"MCP tools available: {[t.name for t in mcp_tools]}")

                messages = list(history or []) + [{"role": "user", "content": question}]
                trace: list[dict] = []

                for iteration in range(1, MAX_ITERATIONS + 1):
                    response = self.client.messages.create(
                        model=MODEL,
                        max_tokens=4096,
                        system=SYSTEM_PROMPT,
                        tools=claude_tools,
                        messages=messages,
                    )

                    # No more tool calls -> final grounded answer.
                    if response.stop_reason != "tool_use":
                        answer = "".join(
                            b.text for b in response.content if b.type == "text"
                        )
                        return AgentResult(answer=answer, iterations=iteration, tool_calls=trace)

                    # Execute every tool call in this turn, collect results.
                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue

                        if block.name == "ask_user":
                            q = block.input.get("question", "")
                            self._log("clarify", q)
                            user_reply = self.ask_user_handler(q)
                            self._log("user reply", user_reply)
                            result_text = json.dumps({"user_answer": user_reply})
                            is_error = False
                        else:
                            self._log("tool call", f"{block.name}({json.dumps(block.input)})")
                            mcp_result = await session.call_tool(block.name, block.input)
                            result_text = "".join(
                                c.text for c in mcp_result.content if c.type == "text"
                            )
                            # Surface server-side rejections/SQL errors (and hard
                            # tool failures) as tool errors so the model treats
                            # them as observations to recover from.
                            is_error = _result_is_error(mcp_result, result_text)
                            preview = result_text[:160].replace("\n", " ")
                            self._log("observe" if not is_error else "OBSERVE-ERROR", preview)

                        trace.append({
                            "iteration": iteration,
                            "tool": block.name,
                            "input": block.input,
                            "result_preview": result_text[:200],
                            "is_error": is_error,
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                            "is_error": is_error,
                        })

                    messages.append({"role": "user", "content": tool_results})

                return AgentResult(
                    answer="(stopped: reached the maximum number of reasoning steps)",
                    iterations=MAX_ITERATIONS,
                    tool_calls=trace,
                )


def ask_sync(question: str, **kwargs) -> AgentResult:
    """Convenience wrapper so callers don't need to manage asyncio."""
    agent = DatabaseAgent(**kwargs)
    return asyncio.run(agent.ask(question))


if __name__ == "__main__":
    # Run as a script: auto-detect the provider from the environment so
    # `python agent/agent.py "..."` works with whichever key is configured
    # (OpenAI or Anthropic), not just Anthropic. (.env is already loaded above.)
    q = " ".join(sys.argv[1:]) or "Fetch employee details where department = 'AI'"
    print(f"\nQUESTION: {q}\n")
    provider = os.environ.get("AGENT_PROVIDER", "").lower() or (
        "openai" if os.environ.get("OPENAI_API_KEY") else "anthropic"
    )
    if provider == "openai":
        from agent_openai import OpenAIDatabaseAgent  # sibling module (script dir on path)
        agent = OpenAIDatabaseAgent()
    else:
        agent = DatabaseAgent()
    result = asyncio.run(agent.ask(q))
    print(f"\nANSWER ({result.iterations} iterations):\n{result.answer}")
