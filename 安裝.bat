@echo off
rem AI Server Kit - Windows installer (double-click). Extra options are passed to install.ps1, e.g. -Lan
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
