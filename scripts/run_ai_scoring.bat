@echo off
cd /d "%~dp0.."
set PYTHONUTF8=1
python -m ai_scoring.run_ai_scoring %*
pause
