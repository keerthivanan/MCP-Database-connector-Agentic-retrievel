"""
Builds the dummy company SQLite database used for this exercise.

Creates three related tables (employees, projects, issues) with foreign-key
relationships and realistic sample data, so that answering non-trivial
questions requires a multi-table JOIN.

Usage:
    python db/init_db.py [--path PATH] [--force]
"""

from __future__ import annotations

import argparse
import os
import sqlite3

SCHEMA = """
CREATE TABLE employees (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    department  TEXT NOT NULL,
    role        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    hire_date   TEXT NOT NULL
);

CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    department  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'completed', 'on_hold')),
    start_date  TEXT NOT NULL
);

CREATE TABLE issues (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    assignee_id  INTEGER REFERENCES employees(id),
    title        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'closed')),
    priority     TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_issues_project  ON issues(project_id);
CREATE INDEX idx_issues_assignee ON issues(assignee_id);
CREATE INDEX idx_employees_dept  ON employees(department);
"""

EMPLOYEES = [
    (1,  "Priya Sharma",     "AI",       "ML Engineer",          "priya.sharma@corp.io",     "2022-01-10"),
    (2,  "Daniel Kim",       "AI",       "Research Scientist",   "daniel.kim@corp.io",       "2021-11-03"),
    (3,  "Amara Okafor",     "AI",       "ML Engineer",          "amara.okafor@corp.io",     "2023-03-21"),
    (4,  "Liam Chen",        "AI",       "Team Lead",            "liam.chen@corp.io",        "2020-06-15"),
    (5,  "Sofia Rossi",      "Platform", "Backend Engineer",     "sofia.rossi@corp.io",      "2021-02-01"),
    (6,  "Noah Williams",    "Platform", "Backend Engineer",     "noah.williams@corp.io",    "2022-09-12"),
    (7,  "Emma Johansson",   "Platform", "DevOps Engineer",      "emma.johansson@corp.io",   "2020-01-20"),
    (8,  "Ravi Patel",       "Platform", "Team Lead",            "ravi.patel@corp.io",       "2019-08-05"),
    (9,  "Mia Nakamura",     "Design",   "Product Designer",     "mia.nakamura@corp.io",     "2022-04-18"),
    (10, "Lucas Fernandez",  "Design",   "UX Researcher",        "lucas.fernandez@corp.io",  "2023-01-09"),
    (11, "Isabella Rocha",   "Sales",    "Account Executive",    "isabella.rocha@corp.io",   "2021-07-22"),
    (12, "Ethan Brooks",     "Sales",    "Sales Manager",        "ethan.brooks@corp.io",     "2018-11-30"),
    (13, "Grace Müller",     "Ops",      "Ops Analyst",          "grace.muller@corp.io",     "2022-12-01"),
    (14, "Kenji Watanabe",   "Ops",      "Ops Manager",          "kenji.watanabe@corp.io",   "2019-03-14"),
    (15, "Zoe Anderson",     "AI",       "Data Scientist",       "zoe.anderson@corp.io",     "2023-06-05"),
]

PROJECTS = [
    (1, "Project Phoenix",      "AI",       "active",    "2023-01-15"),
    (2, "Project Atlas",        "AI",       "active",    "2023-05-01"),
    (3, "Platform Migration",   "Platform", "active",    "2022-10-01"),
    (4, "Observability Revamp", "Platform", "completed", "2022-02-01"),
    (5, "Design System 2.0",    "Design",   "active",    "2023-02-10"),
    (6, "Q4 Sales Push",        "Sales",    "on_hold",   "2023-09-01"),
]

# (id, project_id, assignee_id, title, status, priority, created_at)
ISSUES = [
    (1,  1, 1,  "Model drift on retrieval ranking",            "open",        "high",     "2024-01-05"),
    (2,  1, 2,  "Add eval harness for hallucination rate",     "in_progress", "medium",   "2024-01-08"),
    (3,  1, 4,  "Latency regression after upgrade",            "open",        "critical", "2024-02-01"),
    (4,  1, 3,  "Flaky integration test in pipeline",          "closed",      "low",      "2023-12-20"),
    (5,  1, 1,  "Support multi-turn context window",           "open",        "medium",   "2024-02-14"),
    (6,  2, 15, "Data labeling backlog",                       "open",        "medium",   "2024-01-22"),
    (7,  2, 2,  "Feature store schema mismatch",                "in_progress", "high",     "2024-02-03"),
    (8,  2, 4,  "Onboarding doc for new ML hires",              "closed",      "low",      "2023-11-11"),
    (9,  3, 5,  "Database connection pool exhaustion",          "open",        "critical", "2024-01-30"),
    (10, 3, 6,  "Migrate auth service to new cluster",          "in_progress", "high",     "2024-02-05"),
    (11, 3, 7,  "CI pipeline timeouts on migration branch",     "open",        "medium",   "2024-02-10"),
    (12, 3, 8,  "Rollback plan for phase 2",                     "closed",      "medium",   "2023-10-15"),
    (13, 4, 7,  "Dashboard alert noise reduction",               "closed",      "low",      "2022-03-01"),
    (14, 4, 5,  "Tracing gaps in async workers",                 "closed",      "medium",   "2022-04-12"),
    (15, 5, 9,  "Component library accessibility audit",         "open",        "high",     "2024-01-18"),
    (16, 5, 10, "User research synthesis for v2",                 "in_progress", "medium",   "2024-02-01"),
    (17, 5, 9,  "Design tokens inconsistent across platforms",   "open",        "low",      "2024-02-20"),
    (18, 6, 11, "Renegotiate enterprise contract terms",         "open",        "high",     "2024-01-25"),
    (19, 6, 12, "Sales collateral outdated for Q4 push",          "closed",      "low",      "2023-12-01"),
    (20, 2, 15, "Duplicate records in training set",              "open",        "medium",   "2024-02-18"),
    (21, 1, None, "Unassigned: investigate GPU OOM in batch job", "open",        "high",     "2024-02-22"),
    (22, 3, 6,  "Secrets rotation for migrated services",         "open",        "critical", "2024-02-25"),
    (23, 4, None, "Archive legacy dashboards",                    "closed",      "low",      "2022-05-20"),
    (24, 5, 10, "Usability test recruiting",                      "open",        "low",      "2024-02-27"),
    (25, 2, 3,  "Vector index rebuild after schema change",       "in_progress", "high",     "2024-02-15"),
]


def build_database(db_path: str, force: bool = False) -> None:
    if os.path.exists(db_path):
        if not force:
            raise FileExistsError(
                f"{db_path} already exists. Pass --force to overwrite."
            )
        os.remove(db_path)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO employees (id, name, department, role, email, hire_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            EMPLOYEES,
        )
        conn.executemany(
            "INSERT INTO projects (id, name, department, status, start_date) "
            "VALUES (?, ?, ?, ?, ?)",
            PROJECTS,
        )
        conn.executemany(
            "INSERT INTO issues (id, project_id, assignee_id, title, status, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ISSUES,
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Created {db_path}")
    print(f"  employees: {len(EMPLOYEES)}")
    print(f"  projects:  {len(PROJECTS)}")
    print(f"  issues:    {len(ISSUES)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        default=os.path.join(os.path.dirname(__file__), "company.db"),
        help="Path to the SQLite database file to create.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite the database if it already exists."
    )
    args = parser.parse_args()
    build_database(args.path, force=args.force)
