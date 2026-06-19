@echo off
setlocal
cd /d "%~dp0..\web"
set "PATH=C:\Program Files\nodejs;%PATH%"
npx playwright install chromium
