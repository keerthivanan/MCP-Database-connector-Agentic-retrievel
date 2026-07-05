"""
Builds the dummy company database on PostgreSQL.

Same three related tables and sample data as the SQLite version (the seed
data is imported from init_db.py so there is exactly one source of truth),
plus one thing SQLite cannot give us:

    A REAL DATABASE-PERMISSION LAYER.

This script creates a dedicated role `mcp_readonly` that is granted
SELECT — and only SELECT — on the tables. The MCP server connects as that
role, so even if every software guard failed, PostgreSQL itself would
refuse INSERT/UPDATE/DELETE/DDL at the permission level. That is the
"DB-permission-level" layer of the defense-in-depth story.

Usage:
    # Admin credentials are needed ONCE, to create the database and role:
    set PGADMIN_PASSWORD=<your postgres superuser password>
    python db/init_db_postgres.py

Afterwards, the MCP server only ever uses the low-privilege role:
    set COMPANY_DB_DSN=postgresql://mcp_readonly:readonly_pass@localhost:5432/company
"""

from __future__ import annotations

import os
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Single source of truth for seed data: reuse the SQLite script's rows.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from init_db import EMPLOYEES, PROJECTS, ISSUES

PG_HOST = os.environ.get("PGHOST", "localhost")
PG_PORT = int(os.environ.get("PGPORT", "5432"))
PG_ADMIN_USER = os.environ.get("PGADMIN_USER", "postgres")
PG_ADMIN_PASSWORD = os.environ.get("PGADMIN_PASSWORD", "")

DB_NAME = "company"
READONLY_ROLE = "mcp_readonly"
READONLY_PASSWORD = os.environ.get("MCP_READONLY_PASSWORD", "readonly_pass")

SCHEMA = """
DROP TABLE IF EXISTS issues;
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS employees;

CREATE TABLE employees (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL,
    role        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    hire_date   DATE NOT NULL
);

CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    department  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'completed', 'on_hold')),
    start_date  DATE NOT NULL
);

CREATE TABLE issues (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    assignee_id  INTEGER REFERENCES employees(id),
    title        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'closed')),
    priority     TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    created_at   DATE NOT NULL
);

CREATE INDEX idx_issues_project  ON issues(project_id);
CREATE INDEX idx_issues_assignee ON issues(assignee_id);
CREATE INDEX idx_employees_dept  ON employees(department);
"""


def main() -> None:
    if not PG_ADMIN_PASSWORD:
        sys.exit(
            "Set PGADMIN_PASSWORD to your postgres superuser password "
            "(needed once, to create the database and the read-only role)."
        )

    # --- 1. Create the database (connect to the default 'postgres' db) ----
    admin = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_ADMIN_USER,
        password=PG_ADMIN_PASSWORD, dbname="postgres",
    )
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{DB_NAME}"')
            print(f"Created database '{DB_NAME}'")
        else:
            print(f"Database '{DB_NAME}' already exists")
    admin.close()

    # --- 2. Create schema + data inside the company database --------------
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_ADMIN_USER,
        password=PG_ADMIN_PASSWORD, dbname=DB_NAME,
    )
    with conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.executemany(
            "INSERT INTO employees (id, name, department, role, email, hire_date) "
            "VALUES (%s, %s, %s, %s, %s, %s)", EMPLOYEES,
        )
        cur.executemany(
            "INSERT INTO projects (id, name, department, status, start_date) "
            "VALUES (%s, %s, %s, %s, %s)", PROJECTS,
        )
        cur.executemany(
            "INSERT INTO issues (id, project_id, assignee_id, title, status, priority, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)", ISSUES,
        )

        # --- 3. The DB-permission layer: a role that can ONLY read --------
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READONLY_ROLE,))
        if not cur.fetchone():
            cur.execute(
                f"CREATE ROLE {READONLY_ROLE} LOGIN PASSWORD %s", (READONLY_PASSWORD,)
            )
            print(f"Created role '{READONLY_ROLE}'")
        cur.execute(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {READONLY_ROLE}")
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {READONLY_ROLE}")
        cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READONLY_ROLE}")
        # No INSERT/UPDATE/DELETE/DDL grants — writes are impossible for
        # this role even if every software guard above it failed.
    conn.close()

    print(f"Seeded: employees={len(EMPLOYEES)}, projects={len(PROJECTS)}, issues={len(ISSUES)}")
    print("\nMCP server connection (read-only role — put this in the SERVER env only):")
    print(f"  COMPANY_DB_DSN=postgresql://{READONLY_ROLE}:{READONLY_PASSWORD}"
          f"@{PG_HOST}:{PG_PORT}/{DB_NAME}")


if __name__ == "__main__":
    main()
