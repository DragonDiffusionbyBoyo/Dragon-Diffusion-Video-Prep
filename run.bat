@echo off
echo ============================================
echo  Dragon Diffusion Video Prep
echo ============================================
echo.

:: Check venv exists
if not exist venv\Scripts\activate.bat (
    echo ERROR: Virtual environment not found.
    echo Please run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo Starting app - browser will open automatically...
echo To stop the app, close this window or press Ctrl+C
echo.
python app.py
pause
