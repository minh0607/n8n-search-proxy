@echo off
title n8n Search Proxy - Install
echo ============================================
echo   Installing dependencies...
echo ============================================
echo.

cd /d "%~dp0"
pip install -r requirements.txt

echo.
echo ============================================
echo   Done! Run start.bat to launch the server.
echo ============================================
pause
