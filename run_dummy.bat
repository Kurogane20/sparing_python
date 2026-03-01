@echo off
REM ============================================================
REM AQMS Monitoring System - Dummy Mode (Testing)
REM ============================================================

title AQMS Monitoring System [DUMMY MODE]

echo ============================================================
echo   AQMS MONITORING SYSTEM - DUMMY MODE
echo   Mode simulasi tanpa hardware sensor
echo ============================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan!
    echo Silakan install Python 3.10+ dari https://python.org
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv" (
    echo [INFO] Membuat virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install dependencies if needed
if not exist "venv\Lib\site-packages\PyQt6" (
    echo [INFO] Menginstall dependencies...
    pip install -r requirements.txt
)

echo.
echo [INFO] Menjalankan AQMS dalam mode DUMMY...
echo [INFO] Interval sensor diset ke 5 detik untuk testing
echo.

REM Run the application in dummy mode with shorter interval
python main.py --dummy --interval 5

REM Deactivate on exit
call venv\Scripts\deactivate.bat
