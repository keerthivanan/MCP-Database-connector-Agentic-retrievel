# MCP Database Assistant

A web app where you ask questions about a company database in plain English, and
an AI agent answers them — but the AI can reach the database **only** through the
**Model Context Protocol (MCP)**. No connection strings in the AI, no arbitrary
SQL, read-only by construction.

The app is a **React** chat UI over a **FastAPI** backend. Every question flows:

```
Browser (React)  ->  FastAPI  ->  AI agent  ->  MCP server  ->  PostgreSQL
                                  (holds only      (holds the DB      (read-only
                                   the LLM key)     credentials)        role)
```

The AI never sees the database credentials — they live in the MCP server process
only. Every query is validated (SELECT-only) and the database role itself is
read-only, so a write is physically impossible from three independent layers.

---

## Run it

**One click:** double-click **`run.bat`** — it starts the server and the app
opens at **http://localhost:8000**.

**Or from a terminal:**

```powershell
pip install -r requirements.txt          # first time only
python -m uvicorn backend.api:api --port 8000
```

Then open http://localhost:8000.

Configuration lives in `.env` (copy `.env.example` to `.env` and fill in your
values). You need one LLM key: `OPENAI_API_KEY` **or** `ANTHROPIC_API_KEY`.

---

## Database: PostgreSQL or SQLite

- **PostgreSQL** (used when `COMPANY_DB_DSN` is set in `.env`). Seed it once:
  ```powershell
  $env:PGADMIN_PASSWORD="<your postgres password>"
  python db/init_db_postgres.py     # creates the DB + a SELECT-only role
  ```
- **SQLite** (zero-setup fallback when `COMPANY_DB_DSN` is not set):
  ```powershell
  python db/init_db.py --force      # builds db/company.db
  ```

Switching engines is a server-side change only — the AI agent code never changes.

---

## Project structure

```
frontend/      React chat UI (source + built dist/ that FastAPI serves)
backend/       FastAPI: serves the UI and the /api endpoints
agent/         the AI agent loop; reaches the DB only through MCP tools
mcp_server/    MCP server: 3 tools (list_tables, describe_schema, run_query),
               SQL guardrails, and a tamper-proof audit log
db/            database schema + seed data (PostgreSQL and SQLite)
run.bat        one-click launcher
Dockerfile / render.yaml   deployment (one container serves UI + API)
```

## API endpoints

| Endpoint | What it does |
|---|---|
| `GET  /api/meta` | Which database + LLM provider are active |
| `POST /api/ask` | Ask a question; returns the answer + the agent's tool-call trace |
| `GET  /api/tables` | Browse all tables (fetched through MCP) |
| `POST /api/guard_demo` | Sends a raw `DELETE` to prove it is rejected server-side |
| `GET  /api/audit` | The server-side audit log of every tool call |

---

## Why MCP (not a raw connection string)

- **Credentials stay out of the AI.** They live in the MCP server's environment,
  never in the model's context — so they can't be leaked or prompt-injected out.
- **A narrow, auditable surface.** The AI can call exactly three tools, each with
  a typed schema — not "any SQL it wants."
- **Enforcement the AI can't bypass.** Read-only is enforced in server code and
  by the database role, on the other side of a process boundary from the model.
- **Portability.** SQLite ↔ PostgreSQL is a server-side switch; the agent is
  unchanged. The same agent could point at a different MCP server entirely.
