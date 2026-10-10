@echo off
REM Starts the ALO API + dashboard on http://localhost:8000 (keep this window open while browsing).
cd /d "%~dp0"
start "" http://localhost:8000
python -m uvicorn api.main:app --port 8000
pause
