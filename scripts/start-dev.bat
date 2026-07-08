@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  Vibe-Trading - One-click dev launcher (Windows Batch)
::  Double-click to start backend + frontend dev servers
:: ============================================================

set ROOT=%~dp0..
pushd "%ROOT%"

echo.
echo  ========================================
echo    Vibe-Trading Dev Environment
echo  ========================================
echo.

:: -- Config ------------------------------------------------
set BACKEND_HOST=127.0.0.1
set BACKEND_PORT=8899
set FRONTEND_HOST=127.0.0.1
set FRONTEND_PORT=5899

if not "%VIBE_BACKEND_PORT%"=="" set BACKEND_PORT=%VIBE_BACKEND_PORT%
if not "%VIBE_FRONTEND_PORT%"=="" set FRONTEND_PORT=%VIBE_FRONTEND_PORT%

:: -- Detect Python -----------------------------------------
set PYTHON_BIN=python
if not "%PYTHON%"=="" set PYTHON_BIN=%PYTHON%
if exist ".venv\Scripts\python.exe" set PYTHON_BIN=.venv\Scripts\python.exe
if exist "agent\.venv\Scripts\python.exe" set PYTHON_BIN=agent\.venv\Scripts\python.exe

:: -- Start Backend -----------------------------------------
echo [1/2] Starting backend API server (port %BACKEND_PORT%)...
set PYTHONPATH=%ROOT%\agent
start "Vibe-Trading Backend" /MIN cmd /c "cd /d "%ROOT%\agent" && "%PYTHON_BIN%" -c "import sys; sys.path.insert(0,'.'); from api_server import serve_main; raise SystemExit(serve_main(['--host','%BACKEND_HOST%','--port','%BACKEND_PORT%']))""
echo        backend starting, please wait...

:: -- Wait for backend --------------------------------------
echo        waiting for backend...
set /a ATTEMPTS=0
:wait_backend
timeout /t 2 /nobreak >nul
set /a ATTEMPTS+=1
curl -s -o nul http://127.0.0.1:%BACKEND_PORT%/health 2>nul
if %errorlevel% equ 0 goto backend_ready
if %ATTEMPTS% geq 30 goto backend_timeout
goto wait_backend

:backend_timeout
echo        [WARN] Backend not ready after 30s, continuing...
goto start_frontend

:backend_ready
echo        backend ready

:: -- Start Frontend ----------------------------------------
:start_frontend
echo.
echo [2/2] Starting frontend dev server (port %FRONTEND_PORT%)...

if not exist "frontend\node_modules" (
    echo        installing frontend dependencies...
    cd frontend
    call npm install
    cd ..
)

set VITE_API_URL=http://127.0.0.1:%BACKEND_PORT%
start "Vibe-Trading Frontend" /MIN cmd /c "cd /d "%ROOT%\frontend" && npx vite --host %FRONTEND_HOST% --port %FRONTEND_PORT%"

:: -- Wait for frontend -------------------------------------
echo        waiting for frontend...
set /a ATTEMPTS=0
:wait_frontend
timeout /t 2 /nobreak >nul
set /a ATTEMPTS+=1
curl -s -o nul http://127.0.0.1:%FRONTEND_PORT% 2>nul
if %errorlevel% equ 0 goto frontend_ready
if %ATTEMPTS% geq 30 goto frontend_timeout
goto wait_frontend

:frontend_timeout
echo        [WARN] Frontend not ready after 30s
goto done

:frontend_ready
echo        frontend ready

:: -- Done --------------------------------------------------
:done
echo.
echo  ========================================
echo    Vibe-Trading Dev Environment
echo  ========================================
echo    Frontend : http://127.0.0.1:%FRONTEND_PORT%
echo    Backend  : http://127.0.0.1:%BACKEND_PORT%
echo    API Docs : http://127.0.0.1:%BACKEND_PORT%/docs
echo  ========================================
echo.

start http://127.0.0.1:%FRONTEND_PORT%

echo Press any key to close this window (services keep running)...
pause >nul
popd
endlocal