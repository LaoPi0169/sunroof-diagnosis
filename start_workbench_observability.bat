@echo off
rem Week 16 observability workbench launcher (deliverer only; business testers use the browser only)
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
echo   Week 16 Observability Workbench
echo   URL: http://127.0.0.1:8016
echo   Keep this window open while testing.
echo   Press Ctrl+C to stop the server.
echo ==================================================
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8016/"
.venv\Scripts\python.exe -m uvicorn workbench_observability.app:app --host 127.0.0.1 --port 8016
pause
