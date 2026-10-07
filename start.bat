@echo off
setlocal EnableDelayedExpansion
title LocalVideoSubber

cd /d "%~dp0"

echo ==================================================================
echo   LocalVideoSubber - Local Video Subtitle Workbench
echo ==================================================================
echo.

REM ---------- 1. Locate Python ----------
set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY ( where python >nul 2>&1 && set PY=python )
if not defined PY (
    echo [X] Python not found. Install Python 3.10+ and check "Add to PATH".
    pause
    exit /b 1
)

REM ---------- 2. Backend dependencies ----------
echo [1/4] Checking backend dependencies...
%PY% -c "import fastapi, uvicorn, faster_whisper, yaml, requests, sse_starlette" >nul 2>&1
if errorlevel 1 (
    echo       Installing backend dependencies - first run, takes 5-15 min...
    %PY% -m pip install -r requirements.txt fastapi "uvicorn[standard]" sse-starlette
    if errorlevel 1 (
        echo [X] Backend dependency install failed.
        pause
        exit /b 1
    )
)
echo       Backend dependencies OK

REM ---------- 3. Frontend deps and build ----------
echo [2/4] Checking frontend...
where npm >nul 2>&1
if errorlevel 1 (
    echo [X] Node.js / npm not found. Install from https://nodejs.org/
    pause
    exit /b 1
)
cd web
if not exist node_modules (
    echo       Installing frontend dependencies - first run, takes 2-5 min...
    call npm install
    if errorlevel 1 (
        echo [X] Frontend dependency install failed.
        pause
        exit /b 1
    )
)
if not exist dist (
    echo       Building frontend...
    call npm run build
    if errorlevel 1 (
        echo [X] Frontend build failed.
        pause
        exit /b 1
    )
)
cd ..

REM ---------- 4. FFmpeg ----------
echo [3/4] Checking FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo   [!] FFmpeg not found. Transcribe and render will fail.
    echo       Install: winget install Gyan.FFmpeg
    pause
    exit /b 1
)
echo       FFmpeg OK

REM ---------- Start ----------
echo [4/4] Starting server...
echo.
echo   Browser will open http://127.0.0.1:8000
echo   Close this window to stop the server.
echo ==================================================================
echo.

set PYTHONPATH=%~dp0src
start "" http://127.0.0.1:8000
%PY% -m uvicorn lvs.api:app --app-dir src --host 127.0.0.1 --port 8000

endlocal
