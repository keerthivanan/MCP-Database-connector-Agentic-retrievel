"""
Database backends for the MCP server.

Two interchangeable engines behind the SAME three tools:

  * SQLiteBackend    — zero-setup local file, opened read-only (mode=ro)
  * PostgresBackend  — production-style: connects as the low-privilege
                       `mcp_readonly` role (SELECT-only grants) and opens
                       every transaction read-only

Which one runs is decided by the SERVER's environment (COMPANY_DB_DSN set
-> Postgres; otherwise SQLite). The agent/LLM cannot see or influence this —
it just calls the same three tools either way. Swapping engines with zero
agent changes is the decoupling benefit of the MCP connector layer.
"""

from __future__ import annotations

import os
import sqlite3


def _uniquify(cols: list[str]) -> list[str]:
    """Make column names unique so building per-row dicts never collapses two
    same-named columns (e.g. `SELECT e.id, p.id` -> ['id', 'id_2']). Without
    this, dict(zip(cols, row)) would silently drop the first duplicate."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for c in cols:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out


class SQLiteBackend:
    name = "sqlite"

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)

    def _connect(self):
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"Database not found at {self.db_path}. Run 'python db/init_db.py' first."
            )
        uri = f"file:{self.db_path.replace(os.sep, '/')}?mode=ro"  # engine-level read-only
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def list_tables(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def describe_schema(self, table_name: str) -> dict | None:
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()
            if not exists:
                return None
            columns = [
                {
                    "name": r["name"],
                    "type": r["type"],
                    "nullable": not r["notnull"],
                    "primary_key": bool(r["pk"]),
                }
                for r in conn.execute(f"PRAGMA table_info('{table_name}')")
            ]
            foreign_keys = [
                {
                    "column": r["from"],
                    "references_table": r["table"],
                    "references_column": r["to"],
                }
                for r in conn.execute(f"PRAGMA foreign_key_list('{table_name}')")
            ]
            return {"table": table_name, "columns": columns, "foreign_keys": foreign_keys}
        finally:
            conn.close()

    def execute(self, sql: str) -> tuple[list[str], list[dict]]:
        """Run a (guard-approved) SELECT. Raises DatabaseError on SQL errors."""
        conn = self._connect()
        try:
            cursor = conn.execute(sql)
            cols = _uniquify([d[0] for d in cursor.description]) if cursor.description else []
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
            return cols, rows
        finally:
            conn.close()

    # what the error-recovery loop catches
    error_type = sqlite3.Error


class PostgresBackend:
    name = "postgres"

    def __init__(self, dsn: str):
        import psycopg2  # imported lazily so SQLite mode needs no pg driver
        self._psycopg2 = psycopg2
        self.dsn = dsn
        self.error_type = psycopg2.Error

    def _connect(self):
        conn = self._psycopg2.connect(self.dsn, connect_timeout=5)
        # Read-only at the session level too — third belt on top of the
        # SELECT-only role grants and the SQL guard.
        conn.set_session(readonly=True, autocommit=True)
        return conn

    def list_tables(self) -> list[str]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public' ORDER BY tablename"
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def describe_schema(self, table_name: str) -> dict | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (table_name,),
                )
                col_rows = cur.fetchall()
                if not col_rows:
                    return None

                # NOTE: information_schema.constraint_column_usage only shows
                # constraints on tables the CURRENT ROLE OWNS — and mcp_readonly
                # deliberately owns nothing. Use pg_catalog instead, which is
                # visible to any role that can see the table.
                cur.execute(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                    "  AND a.attnum = ANY(i.indkey) "
                    "WHERE i.indrelid = to_regclass(%s) AND i.indisprimary",
                    (f"public.{table_name}",),
                )
                pk_cols = {r[0] for r in cur.fetchall()}

                cur.execute(
                    "SELECT a.attname, cl2.relname, a2.attname "
                    "FROM pg_constraint con "
                    "JOIN pg_class cl2 ON cl2.oid = con.confrelid "
                    "JOIN unnest(con.conkey)  WITH ORDINALITY AS ck(attnum, ord) ON TRUE "
                    "JOIN unnest(con.confkey) WITH ORDINALITY AS cfk(attnum, ord) "
                    "  ON cfk.ord = ck.ord "
                    "JOIN pg_attribute a  ON a.attrelid = con.conrelid  AND a.attnum = ck.attnum "
                    "JOIN pg_attribute a2 ON a2.attrelid = con.confrelid AND a2.attnum = cfk.attnum "
                    "WHERE con.contype = 'f' AND con.conrelid = to_regclass(%s)",
                    (f"public.{table_name}",),
                )
                foreign_keys = [
                    {"column": c, "references_table": rt, "references_column": rc}
                    for c, rt, rc in cur.fetchall()
                ]

            columns = [
                {
                    "name": name,
                    "type": dtype,
                    "nullable": nullable == "YES",
                    "primary_key": name in pk_cols,
                }
                for name, dtype, nullable in col_rows
            ]
            return {"table": table_name, "columns": columns, "foreign_keys": foreign_keys}
        finally:
            conn.close()

    def execute(self, sql: str) -> tuple[list[str], list[dict]]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cols = _uniquify([d[0] for d in cur.description]) if cur.description else []
                rows = [
                    dict(zip(cols, (str(v) if not isinstance(v, (int, float, type(None))) else v
                                    for v in row)))
                    for row in cur.fetchall()
                ]
                return cols, rows
        finally:
            conn.close()


def get_backend():
    """Pick the engine from the SERVER's environment — never from the LLM."""
    dsn = os.environ.get("COMPANY_DB_DSN")
    if dsn:
        return PostgresBackend(dsn)
    db_path = os.environ.get(
        "COMPANY_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "company.db"),
    )
    return SQLiteBackend(db_path)
