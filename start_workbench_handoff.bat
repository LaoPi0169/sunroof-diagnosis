@echo off
rem Week 18 cold-handoff workbench launcher (deliverer only; testers use the browser only)
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo [ERROR] .venv not found. Create it first:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo.
echo ==================================================
echo   Week 18 Cold-Handoff Workbench (port 8018)
echo   URL: http://127.0.0.1:8018
echo   Keep this window open while testing.
echo   Press Ctrl+C to stop the server.
echo ==================================================
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8018/"
.venv\Scripts\python.exe -m uvicorn workbench_handoff.app:app --host 127.0.0.1 --port 8018
pause
