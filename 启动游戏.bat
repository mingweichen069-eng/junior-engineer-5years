@echo off
title QianShou Engineer
cd /d "%~dp0"
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo Python 3.11+ not found. Install from python.org and check "Add Python to PATH".
  pause
  exit /b 1
)
%PY% -c "import flask" >nul 2>&1
if errorlevel 1 (
  echo Installing Flask ...
  %PY% -m pip install -r requirements.txt
)
echo.
echo Game URL: http://127.0.0.1:5000/
echo Keep this window open.
start "" http://127.0.0.1:5000/
%PY% app.py
pause
