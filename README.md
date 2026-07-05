# MCP Database Connector + Agentic Retrieval

An LLM answers natural-language questions about a company database — but it
can only reach that database through the **Model Context Protocol (MCP)**.
No connection strings in the agent, no arbitrary SQL execution, read-only by
construction, and a plan → act → observe loop with real error recovery.

> See [PLAN.md](PLAN.md) for the full architecture diagram and design
> decisions. This README covers setup, the connector flow, and the required
> conceptual write-up.

---

## 1. Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Build the dummy database (SQLite, 3 related tables, realistic data)
python db/init_db.py --force

# 3. Configure once: copy the example env file and put your key in it.
#    Every entry point loads .env automatically — no per-shell exports needed.
copy .env.example .env       # then edit .env: set OPENAI_API_KEY (or ANTHROPIC_API_KEY)

# 4. Run the full demo (4 scenarios) — or one at a time
python demo.py
python demo.py 2        # just the JOIN scenario
```

The demo auto-detects which key is present (`AGENT_PROVIDER=openai|anthropic`
overrides). Both providers drive the **same MCP server, prompt rules and
guardrails** — `agent/agent.py` (Anthropic) and `agent/agent_openai.py`
(OpenAI) differ only in the LLM client, which is itself a demonstration of
the decoupling argument in §8.

**Prefer a UI?** Run the web application — a **React** (Vite) chat interface
over a **FastAPI** backend, with a live agent-trace panel (every tool call,
error and recovery visible) and the server-side audit log in the sidebar:

```powershell
# one-time: build the React frontend
cd frontend; npm install; npm run build; cd ..

# run (FastAPI serves the React build + the API; config comes from .env)
python -m uvicorn backend:api --port 8000     # open http://localhost:8000
```

Frontend development mode (hot reload): `cd frontend; npm run dev` → http://localhost:5173
(proxies `/api` to the backend on :8000).

**No API key yet?** Run the offline demo — it drives the *same* MCP server
through the same plan → act → observe steps with a scripted planner standing
in for the LLM, so the whole architecture (schema discovery, JOIN, guardrail
rejection, error recovery) is demonstrable with zero credentials:

```bash
python demo_offline.py
```

You can also ask ad-hoc questions:

```bash
python agent/agent.py "Who has the most open critical issues?"
```

---

## 2. What's in the box

| Path | What it is |
|---|---|
| `db/init_db.py` | Builds `db/company.db`: `employees`, `projects`, `issues` with FK links |
| `mcp_server/server.py` | The MCP server. Exposes exactly 3 tools; holds the DB path; opens SQLite **read-only** |
| `mcp_server/sql_guard.py` | The read-only enforcement: SELECT-only allow-list, keyword block-list, single-statement check, hard row LIMIT |
| `agent/agent.py` | The agent (Anthropic/Claude): MCP client + tool-use loop (discover schema → plan SQL → execute → recover → answer) |
| `agent/agent_openai.py` | Same agent, OpenAI driver — shares the prompt/rules from `agent.py`; only the LLM client differs |
| `demo.py` | 4 end-to-end LLM scenarios: simple, JOIN, clarification, error recovery |
| `demo_offline.py` | Same MCP round-trip with a scripted planner — runs with **no API key** |
| `chat.py` | Interactive terminal chat: ask anything live; `/audit` shows the audit log |
| `frontend/` | **React app** (Vite): chat UI with live agent-trace panel, sample-question chips, audit-log sidebar, guardrail-test button — components in `frontend/src/components/` |
| `backend.py` | **FastAPI backend**: `/api/ask`, `/api/audit`, `/api/meta`, `/api/guard_demo`; serves the React build |
| `mcp_server/backends.py` | Engine abstraction: SQLite (default) or PostgreSQL (`COMPANY_DB_DSN`) behind the same 3 tools |
| `db/init_db_postgres.py` | PostgreSQL variant: same data **plus a SELECT-only `mcp_readonly` role** (real DB-permission layer) |
| `mcp_server/audit.log.jsonl` | Tamper-proof server-side audit trail of every tool call (auto-created) |
| `mcp_config.json` | Example MCP host config — note credentials sit in the *server's* env block |
| `PLAN.md` | Architecture diagram + design decisions |

---

## 3. The connector flow (step list)

```
User question
   │
   ▼
Agent (agent/agent.py) ── holds ONLY the Anthropic API key
   │ 1. connects to MCP server (subprocess, stdio JSON-RPC)
   │ 2. discovers available tools dynamically (list_tools)
   │ 3. Claude calls: list_tables  ──────────────┐
   │ 4. Claude calls: describe_schema(table)     │ runtime schema discovery
   │ 5. Claude plans SQL from what it just saw ──┘ (nothing hard-coded)
   │ 6. Claude calls: run_query(sql)
   │ 7. on error → error text returned as tool result → Claude repairs SQL → retry
   │ 8. on ambiguity → ask_user tool → human answers → loop continues
   ▼
MCP server (mcp_server/server.py) ── holds the DB path (env: COMPANY_DB_PATH)
   │ 9. sql_guard validates: SELECT-only, no blocked keywords, one statement,
   │    row LIMIT clamped to ≤500
   ▼
SQLite opened as file:company.db?mode=ro  ← engine-level read-only backstop
   │ 10. rows (or a structured error) travel back up the same path
   ▼
Claude writes a natural-language answer grounded in the returned rows
```

Every hop is observable: the demo prints each tool call, its arguments, and a
preview of each observation, so you can watch the loop think.

---

## 4. How credentials stay out of the agent

- **The agent process** (`agent/agent.py`) contains no DB driver import, no
  connection string, no file path to the database. Its only secret is
  `ANTHROPIC_API_KEY`. Grep it — there is no `sqlite3` in the agent.
- **The MCP server process** reads the DB location from its own environment
  (`COMPANY_DB_PATH`, with a safe default). In `mcp_config.json`, note that
  the `env` block belongs to the *server* entry: the MCP host passes it to
  the server subprocess, and it is never part of any message the LLM sees.
- **The protocol itself** only carries tool names, JSON arguments, and JSON
  results. Even a fully prompt-injected model cannot exfiltrate a credential
  that never enters its context window.

Swapping SQLite for Postgres would mean changing *only* the server: put the
DSN in the server's env, keep the same three tools, and the agent code does
not change at all. That decoupling is the point of the connector layer.

---

## 5. How read-only is enforced (defense in depth)

Safety lives at **three layers**, on purpose — the strongest answer is that no
single layer is trusted alone:

| Layer | Mechanism | What it catches | Why it's insufficient alone |
|---|---|---|---|
| 1. Prompt | System prompt: "READ-ONLY: only SELECT queries" | Keeps a well-behaved model from even trying | Prompts are guidance, not enforcement — models err and can be adversarially steered |
| 2. MCP tool (primary) | `sql_guard.py`: must start with SELECT/WITH; block-list of `INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA/...` with word-boundary matching; comments stripped first so keywords can't hide in them; single-statement only; row LIMIT injected/clamped (default 100, max 500) | Any write, DDL, multi-statement injection, or unbounded scan — rejected *before* the DB sees it | A validator can have bugs |
| 3. Database engine | SQLite opened via `file:...?mode=ro` URI | Any write that somehow survived layer 2 — the engine itself refuses | Doesn't bound read cost; that's layer 2's LIMIT clamp |

**How MCP helps enforce this boundary:** the guard lives in the server
process, on the other side of a process boundary from the LLM. The model
cannot patch, bypass, or "talk its way past" code it can't reach — its entire
universe of action is three named tools whose implementations we control.
Rejections come back as structured tool results (`{"error": ..., "rejected_by":
"sql_guard"}`), which doubles as useful feedback for the agent's recovery loop.

The **row/cost limit**: every query is either wrapped as
`SELECT * FROM (<query>) LIMIT n` or has an oversized `LIMIT` clamped, with a
hard max of 500 rows, plus a connection-level timeout — so neither a
hallucinated `SELECT *` on a big table nor a pathological query can blow up
cost or latency.

**Known trade-off (deliberate):** the guard is keyword-based, not a full SQL
parser, so a blocked word *inside a string literal* (e.g.
`WHERE title LIKE '%create%'`) is rejected too — a **false positive**. We
accept this because (a) the failure mode is safe — it can only over-block,
never let a write through; (b) the agent reads the rejection message and
rephrases the query automatically; and (c) the guard stays ~100 lines of
plain Python that anyone can review line-by-line, with the DB-permission
layer (SELECT-only role) as the backstop. Swapping in an AST-based validator
(e.g. `sqlglot`) later would remove the false positives without touching any
other component.

---

## 6. Agentic depth — what to look for in the demo

- **Plan → act → observe:** the agent *always* starts with `list_tables` +
  `describe_schema` — the system prompt forbids guessing names, and neither
  the agent code nor the prompt contains any table/column name. Rename a
  column in `init_db.py` and rebuild: the agent adapts with zero code change.
- **Error recovery (scenario 4):** the demo feeds the agent a false claim
  that `issues` has a `severity` column and tells it to use that name
  unchecked. The first query fails with SQLite's `no such column: severity`;
  the error comes back as an `is_error` tool result; the agent re-inspects
  the schema, finds the real column (`priority`), and retries successfully.
  The trace prints the whole cycle.
- **Clarification (scenario 3):** "How many unresolved issues are left on the
  migration?" is deliberately ambiguous ('unresolved' = open only, or also
  in_progress? which project is "the migration"?). The agent uses its
  `ask_user` tool to ask one targeted question, then resumes with the answer.
- **Multi-table JOIN (scenario 2):** "Which AI-team members have open issues
  on Project Phoenix?" requires `employees ⋈ issues ⋈ projects` — three
  tables, two foreign keys.
- **Bounded loops:** a hard `MAX_ITERATIONS` cap terminates a question that
  genuinely can't converge, instead of looping forever.

---

## 7. Judgment call: how much schema does the LLM see?

We chose **on-demand introspection** (`list_tables` → `describe_schema`)
over dumping the full schema into every prompt.

- **Cost of our choice:** 1–2 extra tool round-trips per question.
- **What we get:** token cost that scales with the *question*, not the
  *database* — a 300-table production schema would bloat every request if
  inlined, but costs nothing here until a table is actually relevant. It also
  makes the agent schema-change-resilient and keeps the agent code fully
  generic (it works unchanged on any database the server points at).
- **When we'd flip it:** for a tiny stable schema and a high-QPS workload,
  inlining the schema once (with prompt caching) is cheaper. For a 3-table
  demo either works; we deliberately demonstrate the pattern that scales.

---

## 8. Required write-up: MCP as the connector layer between LLM and DB

**What MCP is doing here.** MCP standardizes how an LLM host discovers and
invokes external capabilities: the server *declares* typed tools
(`list_tables`, `describe_schema`, `run_query`), the client *discovers* them
at runtime, and every invocation is a structured JSON-RPC message. In this
project the protocol is the *only* bridge between the model and the data —
an abstraction layer in exactly the sense that a REST API is one between a
frontend and a database.

**Why this beats handing the LLM a raw connection string:**

1. **Credential isolation.** A connection string in the model's context can
   be leaked — echoed into a transcript, logged, or extracted by prompt
   injection. Here the secret lives in a different *process*, configured via
   that process's environment; there is no message type in the protocol that
   could carry it to the model.
2. **A narrow, enumerable capability surface.** With a raw connection the
   model can do anything the driver allows. With MCP it can do exactly three
   things, each with a typed input schema. That surface is small enough to
   review, test, and audit — "what can the model do to our database?" has a
   complete three-line answer.

**Why this beats letting the LLM execute arbitrary SQL directly:**

3. **Enforcement outside the model's control.** "Please only run SELECT" in
   a prompt is a request, not a control. The MCP server validates every query
   in server-side code and opens the database read-only at the engine level.
   A hallucinating or adversarially-prompted model still physically cannot
   write, because the enforcement point is on the other side of a process
   boundary it cannot reach.
4. **Cost and blast-radius bounds.** The server clamps every query to a row
   limit and a timeout — so one bad query can't dump a table or hang a
   connection pool.
5. **Auditability and observability.** Every access is a discrete tool call
   with a name and JSON arguments — trivially loggable and rate-limitable,
   unlike free-form SQL strings buried in a chat transcript.
6. **Decoupling and portability.** The agent depends on tool *names*, not on
   a database engine. SQLite → Postgres is a server-side change only; the
   agent and its prompts stay identical. The same agent could be pointed at a
   different MCP server (a CRM, a ticket system) with no structural change.

**The honest trade-off:** MCP adds a hop (latency), a server to run, and the
tool surface constrains the model to what we anticipated (a genuinely novel
analysis might need a tool we didn't build). For database access those costs
are small and the security/portability gains are decisive — which is why
"tools, not connection strings" is the right default for LLM ↔ data systems.

---

## 9. MCP configuration reference

The agent launches the server itself as a stdio subprocess (see
`DatabaseAgent.__init__`), so no separate process management is needed for
the demo. To use the same server from any other MCP host (e.g. Claude
Desktop), point the host at `mcp_config.json`-style config:

```json
{
  "mcpServers": {
    "company-database": {
      "command": "python",
      "args": ["mcp_server/server.py"],
      "env": { "COMPANY_DB_PATH": "db/company.db" }
    }
  }
}
```

Environment variables:

| Variable | Read by | Purpose |
|---|---|---|
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | agent | LLM access — the agent's only secret |
| `AGENT_PROVIDER` | demo | Force `openai` or `anthropic` (default: auto-detect from which key is set) |
| `COMPANY_DB_PATH` | MCP server | SQLite file path — never visible to the agent/LLM |
| `COMPANY_DB_DSN` | MCP server | Set to a PostgreSQL DSN to switch engines (e.g. `postgresql://mcp_readonly:...@localhost:5432/company`) |
| `MCP_AUDIT_LOG` | MCP server | Audit log location (default `mcp_server/audit.log.jsonl`) |
| `AGENT_MODEL` | agent (Anthropic) | Optional model override (default `claude-opus-4-8`) |
| `OPENAI_AGENT_MODEL` | agent (OpenAI) | Optional model override (default `gpt-4o-mini`) |

### Switching to PostgreSQL

```powershell
# one-time setup (needs your postgres superuser password):
$env:PGADMIN_PASSWORD="<your postgres password>"
python db/init_db_postgres.py     # creates DB 'company' + SELECT-only role 'mcp_readonly'

# then point the MCP SERVER at it (the agent doesn't change at all):
$env:COMPANY_DB_DSN="postgresql://mcp_readonly:readonly_pass@localhost:5432/company"
python demo.py
```

With Postgres, the third defense layer becomes a **real database role with
only `GRANT SELECT`** — even a bug in every software layer above could not
produce a write, because the engine's permission system refuses it.
