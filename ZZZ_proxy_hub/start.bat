@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo 還沒安裝，請先執行 setup.bat
  pause
  exit /b 1
)
if "%ZZZ_PORT%"=="" set ZZZ_PORT=8787
start "" http://127.0.0.1:%ZZZ_PORT%
.venv\Scripts\python -m server
pause
