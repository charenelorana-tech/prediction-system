@echo off
cd /d "%~dp0"

tasklist /FI "IMAGENAME eq mysqld.exe" | find /I "mysqld.exe" >nul
if errorlevel 1 (
    start "" /b "C:\xampp\mysql\bin\mysqld.exe" --defaults-file="C:\xampp\mysql\bin\my.ini" --standalone
    timeout /t 10 >nul
)

set USE_MYSQL=1
set FLASK_DEBUG=0
set DB_HOST=127.0.0.1
set DB_PORT=3306
set DB_USER=root
set DB_PASSWORD=
set DB_NAME=prediction_system
start "" cmd /c "timeout /t 6 >nul & start http://127.0.0.1:8501"
venv\Scripts\python.exe app.py >> app.log 2>&1