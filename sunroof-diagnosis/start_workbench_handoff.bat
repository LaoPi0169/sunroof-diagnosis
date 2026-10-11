@echo off
rem 天窗诊断 · 值守交接工作台 (Week 18 · 端口 8018 · 双角色隔离)
cd /d "%~dp0sunroof-diagnosis"
echo [start] workbench_handoff on http://127.0.0.1:8018  (admin token env: SUNROOF_ADMIN_TOKEN, default sunroof-admin-w18)
start "" http://127.0.0.1:8018
python -m workbench_handoff.app
