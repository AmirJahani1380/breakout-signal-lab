@echo off
setlocal

cd /d "%~dp0"
set "PYTHON=C:\ProgramData\anaconda3\python.exe"
set "BARS_DATA_ROOT=C:\Users\amirj\OneDrive\Desktop\programming\Trade\data analysis\mt5_data"

if not exist "%PYTHON%" (
  echo Python was not found at %PYTHON%
  echo Edit this file and set PYTHON to your Python executable.
  pause
  exit /b 1
)

start "Breakout Chart Server" /min powershell.exe -NoExit -Command "& '%PYTHON%' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000"

