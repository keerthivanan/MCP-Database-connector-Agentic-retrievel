"""
Agent factory: picks the LLM provider from the environment.

Lives in the agent package (not in a demo script) because provider selection
is agent-domain logic — every consumer (CLI demos, terminal chat, web
backend) imports the same factory, proving the MCP layer is provider-
agnostic: swapping Claude <-> GPT is one env var, zero code changes.
"""

from __future__ import annotations

import os
import sys


def make_agent(**kwargs):
    """Return a DatabaseAgent (Anthropic) or OpenAIDatabaseAgent, chosen by
    AGENT_PROVIDER, else by whichever API key is present in the env/.env."""
    provider = os.environ.get("AGENT_PROVIDER", "").lower()
    if not provider:
        if os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            sys.exit(
                "No LLM credentials found. Set OPENAI_API_KEY or ANTHROPIC_API_KEY "
                "in .env (or run the no-key demo: python demos/demo_offline.py)."
            )
    if provider == "openai":
        from agent.agent_openai import OpenAIDatabaseAgent
        return OpenAIDatabaseAgent(**kwargs)
    from agent.agent import DatabaseAgent
    return DatabaseAgent(**kwargs)
