"""
OpenAI variant of the database agent.

Identical architecture to agent.py (the Anthropic version): the LLM reaches
the database ONLY through the MCP server's three tools. Only the LLM
"driver" differs — OpenAI's chat-completions function-calling loop instead
of Anthropic's tool-use loop. This file is the proof of the decoupling
argument in README.md §8: swapping the LLM provider touches nothing but
the agent's LLM client.

Security note: as with agent.py, this file contains NO database
credentials and NO database driver. Its only secret is OPENAI_API_KEY,
read from the environment by the OpenAI SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Callable

from openai import OpenAI
from mcp import ClientSession
from mcp.client.stdio import stdio_client

# Import the shared pieces from the Anthropic agent so the prompt/rules and
# result shape stay identical across providers. Works both as a package
# import (demo.py) and when run directly as a script.
if __package__:
    from .agent import (
        SYSTEM_PROMPT, ASK_USER_TOOL, AgentResult, MAX_ITERATIONS,
        _terminal_ask_user, build_server_params,
    )
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from agent import (
        SYSTEM_PROMPT, ASK_USER_TOOL, AgentResult, MAX_ITERATIONS,
        _terminal_ask_user, build_server_params,
    )

MODEL = os.environ.get("OPENAI_AGENT_MODEL", "gpt-4o-mini")


class OpenAIDatabaseAgent:
    """Connects an OpenAI model to the MCP database server and runs the loop."""

    def __init__(
        self,
        server_command: str | None = None,
        server_args: list[str] | None = None,
        ask_user_handler: Callable[[str], str] | None = None,
        verbose: bool = True,
    ):
        self.server_params = build_server_params(server_command, server_args)
        self.client = OpenAI()  # reads OPENAI_API_KEY from the environment
        self.ask_user_handler = ask_user_handler or _terminal_ask_user
        self.verbose = verbose

    def _log(self, label: str, detail: str = "") -> None:
        if self.verbose:
            print(f"  [{label}] {detail}")

    async def ask(self, question: str, history: list[dict] | None = None) -> AgentResult:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # MCP tool discovery -> OpenAI function-calling tool format.
                mcp_tools = (await session.list_tools()).tools
                openai_tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description or "",
                            "parameters": t.inputSchema,
                        },
                    }
                    for t in mcp_tools
                ] + [
                    {
                        "type": "function",
                        "function": {
                            "name": ASK_USER_TOOL["name"],
                            "description": ASK_USER_TOOL["description"],
                            "parameters": ASK_USER_TOOL["input_schema"],
                        },
                    }
                ]
                self._log("connected", f"MCP tools available: {[t.name for t in mcp_tools]}")

                messages = (
                    [{"role": "system", "content": SYSTEM_PROMPT}]
                    + list(history or [])
                    + [{"role": "user", "content": question}]
                )
                trace: list[dict] = []

                for iteration in range(1, MAX_ITERATIONS + 1):
                    response = self.client.chat.completions.create(
                        model=MODEL,
                        tools=openai_tools,
                        messages=messages,
                    )
                    choice = response.choices[0]

                    # No tool calls -> final grounded answer.
                    if not choice.message.tool_calls:
                        return AgentResult(
                            answer=choice.message.content or "",
                            iterations=iteration,
                            tool_calls=trace,
                        )

                    messages.append(choice.message)

                    for call in choice.message.tool_calls:
                        name = call.function.name
                        args = json.loads(call.function.arguments or "{}")

                        if name == ASK_USER_TOOL["name"]:
                            q = args.get("question", "")
                            self._log("clarify", q)
                            user_reply = self.ask_user_handler(q)
                            self._log("user reply", user_reply)
                            result_text = json.dumps({"user_answer": user_reply})
                            is_error = False
                        else:
                            self._log("tool call", f"{name}({json.dumps(args)})")
                            mcp_result = await session.call_tool(name, args)
                            result_text = "".join(
                                c.text for c in mcp_result.content if c.type == "text"
                            )
                            is_error = '"error"' in result_text[:200]
                            preview = result_text[:160].replace("\n", " ")
                            self._log("observe" if not is_error else "OBSERVE-ERROR", preview)

                        trace.append({
                            "iteration": iteration,
                            "tool": name,
                            "input": args,
                            "result_preview": result_text[:200],
                            "is_error": is_error,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": result_text,
                        })

                return AgentResult(
                    answer="(stopped: reached the maximum number of reasoning steps)",
                    iterations=MAX_ITERATIONS,
                    tool_calls=trace,
                )


def ask_sync(question: str, **kwargs) -> AgentResult:
    agent = OpenAIDatabaseAgent(**kwargs)
    return asyncio.run(agent.ask(question))


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Fetch employee details where department = 'AI'"
    print(f"\nQUESTION: {q}\n")
    result = ask_sync(q)
    print(f"\nANSWER ({result.iterations} iterations):\n{result.answer}")
