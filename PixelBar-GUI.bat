@echo off
rem Double-click to open the PixelBar picture window in your browser.
rem Keep this black window open while you use the page; close it to stop.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python312-embed\python.exe"
if exist "%PY%" goto run
where python >nul 2>nul && (set "PY=python" & goto run)
where py >nul 2>nul && (set "PY=py" & goto run)
echo Python was not found. Install Python 3 and Pillow (pip install pillow), then try again.
pause
exit /b 1
:run
"%PY%" launch_gui.py
pause
