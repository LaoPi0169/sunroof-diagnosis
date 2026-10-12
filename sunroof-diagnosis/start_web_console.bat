@echo off
rem ============================================================
rem  天窗异响诊断 (sunroof-diagnosis) · 交互控制台启动器
rem  端口 8000 · FastAPI application/main.py 网关 + /ui 控制台
rem  防呆: 先检测 8000 占用, 拒绝在脏端口上启动
rem ============================================================
title SUNROOF 天窗诊断交互控制台 (8000)
cd /d "%~dp0"
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>nul
if %errorlevel%==0 (
  echo [阻止启动] 端口 8000 已被占用.
  echo 占用详情 ^(最后一列是 PID^):
  netstat -ano | findstr /R /C:":8000 .*LISTENING"
  echo 处理: taskkill /PID ^<pid^> /F 结束该进程后重跑本脚本.
  pause
  exit /b 1
)
echo ================================================================
echo   天窗异响诊断 · 交互控制台  (FastAPI API 网关面)
echo   URL: http://127.0.0.1:8000/ui     健康检查: /health
echo   接口文档: http://127.0.0.1:8000/docs
echo ================================================================
rem 控制台依赖 fastapi/uvicorn (requirements.txt): 优先用工作区 .venv 的解释器
if exist "%~dp0..\.venv\Scripts\python.exe" (
  set "PYEXE=%~dp0..\.venv\Scripts\python.exe"
) else (
  set "PYEXE=python"
  echo [提示] 未找到工作区 .venv, 回退系统 python —— 若缺 fastapi 请先:
  echo        python -m pip install -r "%~dp0requirements.txt"
)
start "" http://127.0.0.1:8000/ui
"%PYEXE%" -m uvicorn application.main:app --host 127.0.0.1 --port 8000
