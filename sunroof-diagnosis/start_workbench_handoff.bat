@echo off
rem ============================================================
rem  天窗异响诊断 (sunroof-diagnosis) · 值守交接工作台启动器
rem  端口 8018 · 双角色隔离 · 管理员口令 env: SUNROOF_ADMIN_TOKEN
rem  防呆: 工作区根目录另有一个同名 bat, 属课程模板 week-018 参考
rem  实现, 同抢 8018 端口 —— 本脚本先检测占用, 拒绝在脏端口上启动.
rem ============================================================
title SUNROOF 天窗诊断工作台 (8018)
cd /d "%~dp0"
netstat -ano | findstr /R /C:":8018 .*LISTENING" >nul 2>nul
if %errorlevel%==0 (
  echo [阻止启动] 端口 8018 已被占用 —— 很可能是课程模板 week-018 工作台先启动了.
  echo 占用详情 ^(最后一列是 PID^):
  netstat -ano | findstr /R /C:":8018 .*LISTENING"
  echo 处理: taskkill /PID ^<pid^> /F 结束该进程后, 重跑本脚本.
  pause
  exit /b 1
)
echo ================================================================
echo   天窗异响诊断 · 值守交接工作台  (sunroof-diagnosis, 非模板)
echo   URL: http://127.0.0.1:8018     健康检查: /healthz
echo ================================================================
start "" http://127.0.0.1:8018
python -m workbench_handoff.app
