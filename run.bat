@echo off
title IPTV Playlist Updater
cd /d "%~dp0"
echo ========================================================
echo Starting IPTV Playlist Updater...
echo ========================================================
python iptv_updater.py
if errorlevel 1 (
    echo.
    echo [ERROR] An error occurred while running the script.
)
echo.
echo Press any key to exit...
pause > nul
