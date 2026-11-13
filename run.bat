@echo off
setlocal
cd /d "%~dp0"
title Hiring Badge Finder

rem --- find Python (3.12 preferred) ---------------------------------------
set "PY="
py -3.12 --version >nul 2>nul && set "PY=py -3.12"
if not defined PY ( py -3 --version >nul 2>nul && set "PY=py -3" )
if not defined PY ( python --version >nul 2>nul && set "PY=python" )
if not defined PY (
  echo Python was not found. Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)

rem --- virtual environment ----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat" || goto :fail

echo Installing packages (first run takes a minute)...
python -m pip install -r requirements.txt || goto :fail
python -m pip freeze > requirements.lock.txt

echo.
echo Starting Hiring Badge Finder at http://localhost:8000
echo Keep this window open. Press Ctrl+C to stop.
start "" "http://localhost:8000"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof

:fail
echo.
echo Something went wrong. Copy the messages above and send them to your assistant.
pause
exit /b 1
