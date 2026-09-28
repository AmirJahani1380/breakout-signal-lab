@echo off
setlocal

cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python or create the project virtual environment.
  pause
  exit /b 1
)

powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
  echo Port 8000 is already in use. Stop the existing server before starting this one.
  pause
  exit /b 1
)

start "Breakout Chart Server" /min powershell.exe -NoExit -Command "& '%PYTHON%' -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000"
