@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title LocalVideoSubber

cd /d "%~dp0"

echo ==================================================================
echo   LocalVideoSubber - 本地视频字幕工作台
echo ==================================================================
echo.

REM ---------- 1. 定位 Python ----------
set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY ( where python >nul 2>&1 && set PY=python )
if not defined PY (
    echo [X] 未找到 Python。请先安装 Python 3.10+ 并勾选 Add to PATH。
    pause
    exit /b 1
)

REM ---------- 2. 后端依赖 ----------
echo [1/4] 检查后端依赖...
%PY% -c "import fastapi, uvicorn, faster_whisper, yaml, requests, sse_starlette" >nul 2>&1
if errorlevel 1 (
    echo       安装后端依赖（首次约 5-15 分钟）...
    %PY% -m pip install -r requirements.txt fastapi "uvicorn[standard]" sse-starlette
    if errorlevel 1 (
        echo [X] 后端依赖安装失败。
        pause
        exit /b 1
    )
)
echo       后端依赖就绪

REM ---------- 3. 前端依赖与构建 ----------
echo [2/4] 检查前端...
where npm >nul 2>&1
if errorlevel 1 (
    echo [X] 未找到 Node.js / npm。请先安装 https://nodejs.org/
    pause
    exit /b 1
)
cd web
if not exist node_modules (
    echo       安装前端依赖（首次约 2-5 分钟）...
    call npm install
    if errorlevel 1 (
        echo [X] 前端依赖安装失败。
        pause
        exit /b 1
    )
)
if not exist dist (
    echo       构建前端...
    call npm run build
    if errorlevel 1 (
        echo [X] 前端构建失败。
        pause
        exit /b 1
    )
)
cd ..

REM ---------- 4. FFmpeg ----------
echo [3/4] 检查 FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo   [!] 未找到 FFmpeg，转录和渲染会失败。
    echo       安装：winget install Gyan.FFmpeg
    pause
    exit /b 1
)
echo       FFmpeg 就绪

REM ---------- 启动 ----------
echo [4/4] 启动服务...
echo.
echo   浏览器将打开 http://127.0.0.1:8000
echo   关闭本窗口即可停止服务。
echo ==================================================================
echo.

set PYTHONPATH=%~dp0src
start "" http://127.0.0.1:8000
%PY% -m uvicorn lvs.api:app --app-dir src --host 127.0.0.1 --port 8000

endlocal
