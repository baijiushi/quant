@echo off
setlocal
cd /d "%~dp0"
python "%~dp0start_web.py" --prod
if errorlevel 1 (
  echo.
  echo Start failed. Check logs\backend.log.
)
pause
