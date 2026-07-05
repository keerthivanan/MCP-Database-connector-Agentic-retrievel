# ---------------------------------------------------------------------------
# Stage 1: build the React frontend
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python runtime (FastAPI + agent + MCP server, all in one image)
# ---------------------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV PYTHONUNBUFFERED=1

# Rebuilds the demo SQLite DB on every start (deterministic demo data).
# Set COMPANY_DB_DSN as an env var on the platform to use PostgreSQL instead
# (e.g. a free Neon.tech database) — the app switches automatically.
CMD ["sh", "-c", "python db/init_db.py --force && uvicorn backend.api:api --host 0.0.0.0 --port ${PORT:-8000}"]
