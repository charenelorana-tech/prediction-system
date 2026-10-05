@echo off
cd /d "%~dp0"
set R_HOST=shortline.proxy.rlwy.net
set R_PORT=58660
set R_PASS=JYphhgsvIgBPcCpvGbVWZfEStnWvtQNl
venv\Scripts\python.exe sync_to_railway.py > sync.log 2>&1