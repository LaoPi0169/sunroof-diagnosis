@echo off
rem Week 15 adversarial workbench launcher (deliverer only; business testers use the browser only)
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
echo   Week 15 Adversarial / Recovery Workbench
echo   URL: http://127.0.0.1:8015
echo   Keep this window open while testing.
echo   Press Ctrl+C to stop the server.
echo ==================================================
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8015/"
.venv\Scripts\python.exe -m uvicorn workbench_adversarial.app:app --host 127.0.0.1 --port 8015
pause
