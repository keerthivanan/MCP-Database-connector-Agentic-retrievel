"""
SQL read-only guardrail.

Deliberately written with plain string/regex checks (no third-party SQL
parser) so it is easy to read line-by-line and explain to reviewers: this
IS the enforcement point that keeps the LLM from ever mutating or escaping
the database, independent of whatever the model's prompt says.

Design:
  1. Reject anything that isn't a single SELECT / WITH ... SELECT statement.
  2. Reject a fixed block-list of dangerous keywords, even if they appear
     inside the SELECT (e.g. a subquery trying to sneak in a PRAGMA).
  3. Reject multiple statements (a trailing/embedded semicolon).
  4. Clamp/inject a hard row LIMIT so a single query can't return the
     whole table.

This is the "MCP-tool-level" layer of the defense-in-depth described in
PLAN.md / README.md. It is backed up by opening SQLite itself in read-only
mode (see server.py), so even a bug here cannot mutate data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Statement must start with one of these (after stripping comments/whitespace).
ALLOWED_START = ("select", "with")

# Any of these keywords appearing ANYWHERE in the query is an automatic reject.
# This is intentionally broad — it's fine to be over-cautious for a read-only tool.
BLOCKED_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "attach", "detach", "pragma", "vacuum", "reindex",
    "grant", "revoke", "begin", "commit", "rollback", "savepoint",
    "into",  # blocks "SELECT ... INTO", a write disguised as a read
    # PostgreSQL-specific dangerous statements:
    "copy",  # COPY can read/write server-side files
    "do",    # DO executes anonymous procedural code
    "call",  # CALL invokes stored procedures (which may write)
    "set",   # SET changes session state (e.g. turning off read-only)
]

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@dataclass
class GuardResult:
    ok: bool
    sql: str = ""
    reason: str = ""


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* block comments */ so keyword checks
    can't be bypassed by hiding a forbidden word inside a comment trick."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def _contains_blocked_keyword(sql_lower: str) -> str | None:
    for word in BLOCKED_KEYWORDS:
        # \b word boundary so e.g. "created_at" doesn't match "create"
        if re.search(rf"\b{word}\b", sql_lower):
            return word
    return None


def _has_multiple_statements(sql: str) -> bool:
    # Allow one optional trailing semicolon, but nothing after it, and no
    # semicolon anywhere in the middle of the query.
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    return ";" in stripped


def validate_and_limit(sql: str, requested_limit: int | None = None) -> GuardResult:
    """
    Validate that `sql` is a safe, single, read-only SELECT statement, and
    return a (possibly rewritten) query with a hard row limit applied.
    """
    if not sql or not sql.strip():
        return GuardResult(ok=False, reason="Empty query.")

    cleaned = _strip_sql_comments(sql).strip()
    lowered = cleaned.lower().strip()

    if not lowered.startswith(ALLOWED_START):
        return GuardResult(
            ok=False,
            reason=(
                "Only read-only SELECT (or WITH ... SELECT) statements are "
                f"allowed. Query must start with one of {ALLOWED_START}."
            ),
        )

    blocked = _contains_blocked_keyword(lowered)
    if blocked:
        return GuardResult(
            ok=False,
            reason=(
                f"Query rejected: contains the disallowed keyword '{blocked}'. "
                "This tool is read-only — only SELECT queries are permitted."
            ),
        )

    if _has_multiple_statements(cleaned):
        return GuardResult(
            ok=False,
            reason="Only a single SQL statement is allowed (no ';'-separated batches).",
        )

    # Clamp the row limit regardless of what the model asked for.
    limit = requested_limit if requested_limit else DEFAULT_LIMIT
    limit = max(1, min(int(limit), MAX_LIMIT))

    # If the query already has its own LIMIT clause, leave it — but only if
    # that limit is within our max; otherwise wrap it.
    existing_limit_match = re.search(r"\blimit\s+(\d+)\s*;?\s*$", lowered)
    final_sql = cleaned.rstrip().rstrip(";")

    if existing_limit_match:
        existing_limit = int(existing_limit_match.group(1))
        if existing_limit > MAX_LIMIT:
            # Strip the model's LIMIT and replace it with our clamped one.
            final_sql = re.sub(r"\blimit\s+\d+\s*$", "", final_sql, flags=re.IGNORECASE).rstrip()
            final_sql = f"{final_sql} LIMIT {MAX_LIMIT}"
        # else: keep the model's own (smaller/valid) limit as-is
    else:
        final_sql = f"SELECT * FROM ({final_sql}) AS _guarded_subquery LIMIT {limit}"

    return GuardResult(ok=True, sql=final_sql)
