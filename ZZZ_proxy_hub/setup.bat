@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==^> 建立 Python 虛擬環境
if not exist .venv\Scripts\python.exe python -m venv .venv || goto :fail
.venv\Scripts\python -m pip install -U pip wheel || goto :fail

echo ==^> 安裝其他套件
.venv\Scripts\python -m pip install -r requirements.txt || goto :fail

echo ==^> 下載 AI 模型（約 100MB，只需要一次）
.venv\Scripts\python -m server.download_models || goto :fail

echo.
echo 安裝完成！執行 start.bat 啟動網站。
pause
exit /b 0

:fail
echo.
echo 安裝失敗，請把上面的錯誤訊息截圖。
pause
exit /b 1
