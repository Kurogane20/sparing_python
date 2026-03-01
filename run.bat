@echo off
REM ============================================================
REM AQMS Monitoring System - Windows Startup Script
REM ============================================================

title AQMS Monitoring System

echo ============================================================
echo   AQMS MONITORING SYSTEM
echo   Air Quality Monitoring System
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
echo [INFO] Menjalankan AQMS Monitoring System...
echo.

REM Run the application
python main.py %*

REM Deactivate on exit
call venv\Scripts\deactivate.bat
