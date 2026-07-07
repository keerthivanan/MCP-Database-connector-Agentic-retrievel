# Task 2 — MCP Database Connector + Agentic Retrieval
## Full Architecture & Implementation Plan

*Prepared for team review — covers design decisions, architecture, folder layout, and rollout steps.*

---

## 1. Objective (restated)

Stand up a dummy database, expose it to an LLM **only** through the Model Context
Protocol (MCP) — never via raw connection strings — and build a small agentic loop
that:

1. Discovers the schema at runtime (no hard-coded table/column names)
2. Plans a SQL query from a natural-language question
3. Executes it through MCP tools
4. Recovers from bad SQL automatically
5. Returns a grounded, natural-language answer
6. Enforces read-only access + a row/cost limit as a safety boundary

---

## 2. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                              USER                                    │
│              "Which AI-team members have open issues                 │
│               on Project X?"                                         │
└───────────────────────────────┬────────────────────────────────────--┘
                                 │ natural language question
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    AGENT  (agent/agent.py)                           │
│  Plan → Act → Observe loop, driven by Claude (Anthropic API)         │
│                                                                        │
│   1. list_tables()            ──┐                                    │
│   2. describe_schema(table)     │  discovery (no hard-coded schema)  │
│   3. run_query(sql)           ──┘  (LLM plans SQL from schema)       │
│   4. on SQL error → read message → repair SQL → retry (bounded)      │
│   5. synthesize final NL answer, grounded in returned rows           │
│                                                                        │
│   Holds:  Anthropic API key only.  NO DB CREDENTIALS. NO DB DRIVER.  │
└───────────────────────────────┬────────────────────────────────────--┘
                                 │ MCP protocol (stdio transport,
                                 │ JSON-RPC 2.0 tool calls)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│              MCP SERVER  (mcp_server/server.py)                      │
│  Exposes exactly 3 tools over MCP — the ONLY interface the agent     │
│  ever sees:                                                           │
│                                                                        │
│    • list_tables()          → names of tables in the DB              │
│    • describe_schema(table) → columns, types, FKs for one table      │
│    • run_query(sql, limit)  → executes SELECT-only SQL, returns rows │
│                                                                        │
│  Guardrails enforced HERE, not trusted to the LLM:                    │
│    - Plain-Python SQL guard: only SELECT/WITH statements allowed      │
│    - Keyword blocklist rejects INSERT/UPDATE/DELETE/DROP/ALTER/COPY…  │
│    - Hard LIMIT injected/clamped (default 100, max 500 rows)          │
│    - Query timeout                                                    │
│    - Connects to SQLite in read-only URI mode (file:...?mode=ro)      │
│    - Credentials/DB path come from server-side env/config, never     │
│      passed through the protocol                                     │
└───────────────────────────────┬────────────────────────────────────--┘
                                 │ read-only SQLite connection
                                 │ (file:company.db?mode=ro, immutable)
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     DATABASE  (db/company.db)                        │
│   SQLite, 3 related tables + realistic sample rows:                  │
│     employees(id, name, department, role, email, hire_date)          │
│     projects(id, name, department, status, start_date)               │
│     issues(id, project_id → projects.id,                             │
│             assignee_id → employees.id,                              │
│             title, status, priority, created_at)                     │
└──────────────────────────────────────────────────────────────────────┘
```

**Key point for the team:** the arrow between Agent and MCP Server is the *only*
path the LLM has to the data. There is no code path where Claude ever sees a
connection string, a driver import, or unrestricted SQL execution.

---

## 3. Repository layout

```
task_2 project/
├── README.md                ← setup, config, security write-up (deliverable)
├── requirements.txt         ← Python deps (agent, MCP, drivers, FastAPI)
├── .env / .env.example      ← config: LLM key (agent-side) + DB DSN (server-side); git-ignored
├── .gitignore
│
├── db/
│   ├── init_db.py            ← SQLite variant: builds + seeds company.db
│   └── init_db_postgres.py   ← PostgreSQL variant: same data + SELECT-only mcp_readonly role
│
├── mcp_server/
│   ├── server.py             ← the MCP server (stdio): 3 tools, guardrails, audit log
│   ├── sql_guard.py          ← read-only enforcement (SELECT-only allow-list, blocklist, LIMIT clamp)
│   ├── backends.py           ← engine abstraction: SQLite (read-only) / PostgreSQL (read-only role)
│   └── audit.log.jsonl       ← tamper-proof server-side audit trail (auto-created)
│
├── agent/
│   ├── agent.py              ← plan→act→observe loop (Anthropic driver) + shared prompt/rules
│   └── agent_openai.py       ← same loop, OpenAI driver (proves provider decoupling)
│
├── demos/
│   ├── demo.py               ← 4 CLI scenarios: simple, JOIN, clarification, error recovery
│   ├── demo_offline.py       ← same MCP round-trip, scripted planner — no API key needed
│   └── chat.py               ← interactive terminal chat (/audit shows the log)
│
├── docs/
│   ├── PLAN.md               ← this file
│   └── demo_output.txt       ← saved transcript of a full live run (PostgreSQL)
│
├── backend/
│   └── api.py                ← FastAPI: /api/ask /tables /audit /meta /guard_demo + serves React
├── frontend/                 ← React app (Vite): src/App.jsx + components/ (trace, audit, db views)
│
└── mcp_config.json           ← example MCP host config (credentials in the SERVER env block)
```

---

## 4. Database design (Step 5 of scope)

Three related tables, FK-linked so multi-table JOIN reasoning is required:

| Table       | Columns                                                              | Notes |
|-------------|-----------------------------------------------------------------------|-------|
| `employees` | id PK, name, department, role, email, hire_date                      | departments: AI, Platform, Design, Sales, Ops |
| `projects`  | id PK, name, department, status, start_date                          | status: active / completed / on_hold |
| `issues`    | id PK, project_id FK→projects.id, assignee_id FK→employees.id, title, status, priority, created_at | status: open / in_progress / closed |

~15 employees, ~6 projects, ~25 issues — enough for real JOIN/GROUP BY answers
without being a toy of 2 rows.

---

## 5. MCP server design (Step 6 of scope)

**Why MCP instead of giving the LLM a raw connection string or letting it run
arbitrary SQL?** (full write-up also goes in README, short version here for
the team):

- **Capability boundary, not just a driver wrapper.** MCP tools are a fixed,
  named, schema-typed surface (`list_tables`, `describe_schema`, `run_query`).
  The LLM can request *those three actions and nothing else* — it cannot open
  a socket, change the DSN, or call an undeclared method, because the protocol
  only offers what the server chooses to expose.
- **Credentials never enter the LLM's context window.** The connection string /
  DB path lives in the MCP server's process environment. The agent process
  holds only an Anthropic API key. Even a fully compromised prompt cannot leak
  DB credentials it was never given.
- **Enforcement point is outside the LLM's control.** Prompt instructions
  ("please only run SELECT") are guidance, not enforcement — a model can be
  jailbroken or simply err. The MCP server validates every query in plain,
  reviewable Python (`sql_guard.py`) before it reaches the database,
  independent of what the model intended.
- **Transport-level auditability.** Every tool call is a discrete, logged
  JSON-RPC message (tool name + arguments), which makes access review and
  rate/row limiting straightforward in a way "the model wrote some SQL and we
  eval'd it" is not.

**Tools exposed:**

| Tool | Input | Output | Purpose |
|---|---|---|---|
| `list_tables` | — | table names | schema discovery step 1 |
| `describe_schema` | `table_name` | columns, types, PK/FK | schema discovery step 2 |
| `run_query` | `sql`, optional `limit` | rows + column names, or structured error | execution |

**Guardrails (defense in depth — see §7):**

1. SQLite is opened with `file:company.db?mode=ro` (immutable, OS-level
   read-only) — even a bug in the SQL guard cannot mutate data.
2. Every `run_query` call is validated by `sql_guard.py` — deliberately
   written as plain, line-by-line-reviewable Python (allow-list of statement
   starts + keyword blocklist) rather than a third-party SQL parser. Only
   `SELECT`/`WITH` statements are permitted; `INSERT/UPDATE/DELETE/DROP/
   ALTER/ATTACH/PRAGMA/COPY/DO` etc. are rejected before touching the DB.
3. A hard row `LIMIT` (default 100, max 500) is injected/clamped server-side,
   regardless of what the LLM asked for.
4. A statement timeout guards against pathological queries.
5. Errors are returned as structured tool results (not exceptions that crash
   the loop) so the agent can read the message and self-correct.

---

## 6. Agentic workflow design (Step 7 of scope)

Plan → Act → Observe loop, implemented as a manual Claude tool-use loop
(not a single function call):

1. **Discover** — agent always calls `list_tables` then `describe_schema`
   for the relevant tables *at runtime*; the system prompt does not contain
   table/column names. This is what lets the same agent survive a schema
   change without a code edit.
2. **Plan** — Claude drafts a SQL SELECT using only what discovery returned.
3. **Act** — agent calls `run_query` via MCP.
4. **Observe / recover** — if `run_query` returns an error (bad column,
   syntax error), the tool result (`is_error: true` + message) is fed back to
   Claude, which reads the error and emits a corrected query. Bounded to a
   small number of retries so a genuinely broken question terminates instead
   of looping forever.
5. **Clarify** — if the question is ambiguous (e.g. "issues on Project X" but
   no project matches "X" closely, or the question is missing a needed
   filter), the agent asks a follow-up question back to the human instead of
   guessing, then resumes the loop with the answer.
6. **Answer** — once rows come back, Claude writes a grounded NL answer
   citing the actual returned values (not a hallucinated summary).

**Demo questions (Step 8 of scope):**

- Simple: *"Fetch employee details where department = 'AI'"*
- Multi-table JOIN: *"Which AI-team members have open issues on Project
  Phoenix?"* (employees ⋈ issues ⋈ projects)
- Clarification: a deliberately ambiguous question that forces the agent to
  ask the user something before it can safely query
- Error recovery: a seeded bad-column / bad-table scenario showing the agent
  reading the SQLite error and repairing the query

---

## 7. Judgment calls (explicit, as the assessment asks)

**Where does safety live?** — Defense in depth, at three layers:

| Layer | What it enforces | Why it's not enough alone |
|---|---|---|
| Prompt-level | System prompt tells Claude "read-only, SELECT only" | A model can misinterpret or be adversarially steered; not a real boundary |
| MCP-tool-level | `sql_guard.py`: SELECT-only allow-list + keyword blocklist, clamps LIMIT | The strongest layer — enforced in code the LLM can't override — but only as good as the validator |
| DB-permission-level | SQLite opened `mode=ro`; (in Postgres variant: a role with `SELECT`-only grants) | Backstops a bug in the SQL guard; OS/DB itself refuses the write |

No single layer is trusted alone — the SQL guard is the primary control, the
read-only file mode is the backstop, and the prompt is a cheap first filter
that reduces how often the guard even has to reject something.

**How much schema does the LLM see?** — On-demand introspection
(`list_tables` → `describe_schema`) rather than dumping the full schema into
the system prompt every turn.

- *Pro:* scales to large schemas without bloating every request's token cost;
  the agent only pays for the tables actually relevant to the question; the
  agent is schema-change-resilient (a renamed column doesn't require a
  prompt edit).
- *Con:* costs 1–2 extra tool round-trips per question vs. a static schema
  dump.
- *Decision:* for a 3-table demo DB the token cost either way is trivial, but
  the pattern is chosen deliberately to demonstrate the technique the
  assessment is testing for — real production schemas (dozens/hundreds of
  tables) make full-schema-in-prompt impractical, so on-demand introspection
  is the right default to build the habit around.

---

## 8. Conceptual write-up (required by the brief — included in full in README)

MCP acts as an **abstraction/connector layer** between the LLM and the
database, the same way a REST API sits between a frontend and a database: the
LLM only ever calls named, typed tools; the MCP server is the sole component
that holds credentials and knows how to actually talk to SQLite. This is
strictly better than raw connection strings or arbitrary SQL execution
because:

1. **No credential exposure** — secrets never enter the model's context,
   can't be echoed back, logged in a transcript, or exfiltrated via prompt
   injection.
2. **A narrow, auditable capability surface** — the LLM can request exactly
   `list_tables` / `describe_schema` / `run_query`, nothing else; this is
   enumerable and testable, unlike "whatever SQL the model decided to run."
3. **Enforcement outside the LLM's control** — read-only mode and the SQL
   guard live in server code, so a prompt-injected or hallucinating model
   still cannot mutate or exfiltrate beyond what the tool allows.
4. **Decoupling** — the database can change engines (SQLite → Postgres) or
   move hosts without touching the agent code at all; the agent only knows
   about the three tool names.

---

## 9. Build order (what happens next, in this session)

1. `db/init_db.py` — create SQLite schema + seed realistic data
2. `mcp_server/sql_guard.py` — SQL allow-list/blocklist validator (plain Python)
3. `mcp_server/server.py` — MCP server (stdio) wiring the 3 tools to the guard + read-only connection
4. `agent/agent.py` — MCP client + Claude tool-use loop (plan/act/observe, error recovery, clarification)
5. `demos/demo.py` — runs all four demo questions end-to-end, printing the full trace
6. `README.md` — setup instructions, config, security write-up, conceptual explanation
7. Run the demo live and confirm all four scenarios behave as designed

---

## 10. What "done" looks like

- `python db/init_db.py` produces `db/company.db` with realistic, FK-linked data
- `python demos/demo.py` runs four scenarios end-to-end against the live LLM (OpenAI or Anthropic) through the MCP server, with visible tool calls, and prints grounded NL answers
- Attempting a write (e.g. asking the agent to "delete issue 3") is provably rejected at the MCP layer, not just refused by the prompt
- A deliberately malformed request triggers one visible error → repair → success cycle
- README documents the MCP config, connector flow, credential isolation, and defense-in-depth reasoning
