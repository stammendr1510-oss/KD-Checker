@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -B -c "import app"
if errorlevel 1 (
  echo KD Checker could not start. Check Python and install requirements.txt in .venv.
  exit /b 1
)
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe app.py
pause
