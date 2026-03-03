@echo off
setlocal
cd /d %~dp0

set ENV_NAME=waxgame

echo Checking conda...

where conda >nul 2>nul
if %errorlevel% neq 0 (
    echo Conda not found in PATH.
    echo Please install Anaconda/Miniconda and ensure conda is available.
    pause
    exit /b
)

echo Activating environment %ENV_NAME% ...
call conda activate %ENV_NAME%

echo Installing / checking dependencies...
pip install -r requirements.txt

echo Starting game...
python main.py

pause