@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python manual_server.py --open
) else (
  py -3 manual_server.py --open
)
pause
