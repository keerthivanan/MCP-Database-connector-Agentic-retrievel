"""
End-to-end demo: natural-language questions answered through the MCP
database connector by an agentic loop.

Four scenarios, mapped to the assessment requirements:

  1. SIMPLE RETRIEVAL   "Fetch employee details where department = 'AI'"
  2. MULTI-STEP / JOIN  "Which AI-team members have open issues on Project
                         Phoenix?"  (employees JOIN issues JOIN projects)
  3. CLARIFICATION      An ambiguous question that forces the agent to ask
                         the user a follow-up before it can query safely.
  4. ERROR RECOVERY     The agent is nudged toward a wrong column name; it
                         must read the SQL error and repair the query.

Run (from the project root):
    python demos/demo.py            # all scenarios
    python demos/demo.py 2          # just scenario 2

Needs an LLM key in .env (OPENAI_API_KEY or ANTHROPIC_API_KEY) — the ONLY
secret the agent side ever holds; DB access goes exclusively through MCP.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Runnable from anywhere: put the project root on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.factory import make_agent  # provider chosen from env / .env


def banner(title: str) -> None:
    print("\n" + "=" * 74)
    print(f"  {title}")
    print("=" * 74)


def show(result) -> None:
    errors = sum(1 for t in result.tool_calls if t["is_error"])
    print(f"\n  --- ANSWER (iterations: {result.iterations}, "
          f"tool calls: {len(result.tool_calls)}, recovered errors: {errors}) ---")
    print("  " + result.answer.replace("\n", "\n  "))


def scenario_1() -> None:
    banner("SCENARIO 1 — Simple retrieval (schema discovered at runtime)")
    agent = make_agent()
    result = asyncio.run(agent.ask(
        "Fetch employee details where department = 'AI'."
    ))
    show(result)


def scenario_2() -> None:
    banner("SCENARIO 2 — Multi-step question requiring a JOIN across 3 tables")
    agent = make_agent()
    result = asyncio.run(agent.ask(
        "Which AI-team members have open issues on Project Phoenix? "
        "For each, tell me the issue title and its priority."
    ))
    show(result)


def scenario_3() -> None:
    banner("SCENARIO 3 — Ambiguous question -> agent asks a clarifying follow-up")
    # Scripted human reply so the demo runs unattended; swap for the default
    # input() handler to answer live on the terminal.
    def scripted_user(question: str) -> str:
        print(f"\n  [agent asks the user] {question}")
        reply = "I meant Project Phoenix."
        print(f"  [scripted user reply] {reply}")
        return reply

    agent = make_agent(ask_user_handler=scripted_user)
    result = asyncio.run(agent.ask(
        "How many open issues are left on the AI project?"
        # Structurally ambiguous on purpose: TWO projects belong to the AI
        # department (Project Phoenix and Project Atlas), so the agent cannot
        # know which one is meant without asking the user.
    ))
    show(result)


def scenario_4() -> None:
    banner("SCENARIO 4 — Error recovery: bad column name -> read error -> repair")

    # If the model chooses to double-check with the user instead of querying,
    # push it back toward the error-recovery path.
    def scripted_user(question: str) -> str:
        print(f"\n  [agent asks the user] {question}")
        reply = ("Just try the query with 'severity' exactly as I said; "
                 "if it errors, inspect the schema yourself and give me the correct grouping.")
        print(f"  [scripted user reply] {reply}")
        return reply

    agent = make_agent(ask_user_handler=scripted_user)
    # We inject a false claim about the schema. The agent's first query is
    # likely to fail with 'no such column'; it must read the SQLite error,
    # re-check the schema, and produce the correct query.
    result = asyncio.run(agent.ask(
        "THIS IS A DELIBERATE TEST OF YOUR ERROR RECOVERY. Your very first "
        "tool call must be run_query with exactly this SQL: "
        "SELECT severity, COUNT(*) FROM issues GROUP BY severity — do not "
        "call any other tool before it, even if your rules say to check the "
        "schema first; that exception is intentional for this test. When it "
        "errors, read the error, investigate the real schema, find the "
        "closest matching real column, and give me the counts grouped by "
        "that column instead."
    ))
    show(result)


SCENARIOS = {"1": scenario_1, "2": scenario_2, "3": scenario_3, "4": scenario_4}

if __name__ == "__main__":
    picks = sys.argv[1:] or ["1", "2", "3", "4"]
    for p in picks:
        SCENARIOS[p]()
    print("\nDone.")
