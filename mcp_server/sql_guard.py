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
    applied_limit: int = 0  # the effective row cap on the returned query


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* block comments */ so keyword checks
    can't be bypassed by hiding a forbidden word inside a comment trick."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def _blank_string_literals(sql: str) -> str:
    """Replace the CONTENTS of single-quoted string literals with an empty
    literal, so the keyword block-list and the single-statement check run only
    over SQL *syntax*, never over data values. Without this, a legitimate
    read-only search whose value happens to contain a blocked word or a
    semicolon — e.g. `WHERE title LIKE '%training set%'` or `= 'a;b'` — would
    be wrongly rejected. A write still cannot hide here: keywords like INSERT/
    DROP are only meaningful outside quotes, and this only ever removes quoted
    data. SQL's '' escape (a doubled quote inside a string) is handled."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


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
    # Run the safety checks over syntax only — string-literal *contents* are
    # blanked so a data value can never be mistaken for a keyword or a second
    # statement. Keep `cleaned` (literals intact) for the query we actually run.
    checkable = _blank_string_literals(cleaned)
    lowered = checkable.lower().strip()

    # Allow a parenthesized leading SELECT, e.g. "(SELECT ...) UNION (SELECT ...)".
    if not lowered.lstrip("(").lstrip().startswith(ALLOWED_START):
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

    if _has_multiple_statements(checkable):
        return GuardResult(
            ok=False,
            reason="Only a single SQL statement is allowed (no ';'-separated batches).",
        )

    # Clamp the row limit regardless of what the model asked for. Note `is not
    # None` so an explicit limit=0 clamps to the minimum of 1 (not the default).
    limit = requested_limit if requested_limit is not None else DEFAULT_LIMIT
    limit = max(1, min(int(limit), MAX_LIMIT))

    final_sql = cleaned.rstrip().rstrip(";").rstrip()

    # Does the query already END with its own top-level LIMIT [OFFSET] clause?
    # (Detected on the literal-blanked text so a value like '... limit 5' can't
    # match.) If so, keep it (clamping only when it exceeds our max). Otherwise
    # APPEND `LIMIT n` — appending preserves a trailing ORDER BY, which wrapping
    # the query in an outer `SELECT * FROM (...)` would silently discard.
    blanked_final = _blank_string_literals(final_sql)
    tail = re.search(r"\blimit\s+(\d+)(\s+offset\s+\d+)?\s*$", blanked_final, re.IGNORECASE)
    if tail:
        existing_limit = int(tail.group(1))
        offset = tail.group(2) or ""
        if existing_limit > MAX_LIMIT:
            final_sql = final_sql[: tail.start()].rstrip() + f" LIMIT {MAX_LIMIT}{offset}"
            applied = MAX_LIMIT
        else:
            applied = existing_limit  # keep the model's own (valid) limit
    else:
        final_sql = f"{final_sql} LIMIT {limit}"
        applied = limit

    return GuardResult(ok=True, sql=final_sql, applied_limit=applied)
