@echo off
echo ============================================
echo   H4ck - Secure Messenger
echo   4ayka Studio
echo ============================================
echo.

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python не найден. Установите Python 3.10+
    echo     https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [*] Preventing sleep mode...
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change monitor-timeout-ac 0

if not exist "venv" (
    echo [*] Creating virtual environment...
    python -m venv venv
)

echo [*] Installing dependencies...
call venv\Scripts\activate.bat
if exist "requirements.lock" (
    pip install -r requirements.lock -q
) else (
    pip install -r requirements.txt -q
)

if exist "tunnel.json" (
    echo [*] Permanent tunnel configured
) else (
    echo [*] Quick tunnel mode (temporary URL)
    echo     For permanent URL: run setup-tunnel.bat
)

echo.
echo [*] Starting H4ck Messenger...
echo.

python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
