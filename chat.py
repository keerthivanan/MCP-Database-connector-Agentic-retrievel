"""
Interactive chat mode — the live-demo interface.

Type any natural-language question about the company database and watch the
agent work in real time: schema discovery, SQL planning, guarded execution,
error recovery, and clarifying questions (it will ask YOU right here in the
terminal when it needs to).

Run:
    python chat.py

Commands:
    /audit     show the last 10 entries of the server-side audit log
    /quit      exit

Uses whichever LLM key is set (OPENAI_API_KEY or ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from demo import make_agent

AUDIT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "mcp_server", "audit.log.jsonl")


def show_audit(n: int = 10) -> None:
    if not os.path.exists(AUDIT_LOG):
        print("  (no audit log yet)")
        return
    with open(AUDIT_LOG, encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    print(f"\n  Last {len(lines)} audited tool calls (server-side, tamper-proof):")
    for line in lines:
        e = json.loads(line)
        arg = e["args"].get("sql") or e["args"].get("table_name") or ""
        print(f"    {e['ts'][11:19]}  {e['tool']:<16} {e['outcome']:<18} {str(arg)[:60]}")


def main() -> None:
    print("=" * 70)
    print("  Company DB assistant — ask anything (data access via MCP only)")
    print("  Commands: /audit (show server audit log), /quit")
    print("=" * 70)

    while True:
        try:
            question = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye!")
            return

        if not question:
            continue
        if question.lower() in ("/quit", "/exit", "quit", "exit"):
            print("bye!")
            return
        if question.lower() == "/audit":
            show_audit()
            continue

        agent = make_agent()
        result = asyncio.run(agent.ask(question))
        errors = sum(1 for t in result.tool_calls if t["is_error"])
        print(f"\nassistant ({len(result.tool_calls)} tool calls"
              f"{', ' + str(errors) + ' recovered errors' if errors else ''}):")
        print(result.answer)


if __name__ == "__main__":
    main()
