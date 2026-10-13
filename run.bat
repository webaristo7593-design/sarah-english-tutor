@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist ".env" (
  ".venv\Scripts\python.exe" configure.py
  if errorlevel 1 goto failed
)
echo Open http://127.0.0.1:8000 in Chrome or Edge.
".venv\Scripts\python.exe" main.py --web
pause
exit /b
:failed
echo Setup failed. Check that Python 3.11 or newer is installed and added to PATH.
pause
exit /b 1
