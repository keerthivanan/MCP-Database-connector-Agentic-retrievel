@echo off
REM =====================================================================
REM  One-click launcher for the MCP Database Assistant website.
REM  Double-click this file (or run it in a terminal) to start the app.
REM  Then open  http://localhost:8000  in your browser.  Press Ctrl+C to stop.
REM =====================================================================
cd /d "%~dp0"

REM 1. Install Python dependencies on first run (skipped if already present).
python -c "import uvicorn, fastapi, mcp, openai, psycopg2" 2>nul
if errorlevel 1 (
    echo Installing Python dependencies...
    python -m pip install -r requirements.txt
)

REM 2. If NOT using PostgreSQL, build the local SQLite database when missing.
REM    (PostgreSQL is used automatically when COMPANY_DB_DSN is set in .env.)
if not exist "db\company.db" (
    python db\init_db.py --force 2>nul
)

REM 3. Start the web server (serves the React UI + the API + the MCP layer).
echo.
echo   MCP Database Assistant is starting on http://localhost:8000
echo   (Press Ctrl+C to stop.)
echo.
python -m uvicorn backend.api:api --host 127.0.0.1 --port 8000
