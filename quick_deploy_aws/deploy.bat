@echo off
chcp 65001 >nul
setlocal
rem 找 Git Bash（PowerShell / cmd 的 bash 可能是 WSL，裡面沒有 Windows 版的 aws）
set "GITBASH=%ProgramFiles%\Git\bin\bash.exe"
if not exist "%GITBASH%" for /f "delims=" %%G in ('where git 2^>nul') do if not exist "%GITBASH%" set "GITBASH=%%~dpG..\bin\bash.exe"
if not exist "%GITBASH%" (
  echo 找不到 Git Bash，請先安裝 Git for Windows：https://git-scm.com/download/win
  pause
  exit /b 1
)
"%GITBASH%" "%~dp0deploy_all.sh" %*
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" echo 執行失敗（代碼 %ERR%），請看上面的錯誤訊息。
pause
exit /b %ERR%
