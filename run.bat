@echo off
title QuickBooks Co-pilot
cd /d "%~dp0"
echo Checking dependencies (first run only)...
py -m pip install --quiet --disable-pip-version-check fastapi "uvicorn[standard]" >nul 2>&1
if errorlevel 1 ( python -m pip install --quiet --disable-pip-version-check fastapi "uvicorn[standard]" >nul 2>&1 )
echo.
echo   QuickBooks Co-pilot starting at  http://127.0.0.1:8750
echo   (keep this window open; close it to stop)
echo.
start "" http://127.0.0.1:8750
py -m uvicorn app:app --host 127.0.0.1 --port 8750 2>nul || python -m uvicorn app:app --host 127.0.0.1 --port 8750
