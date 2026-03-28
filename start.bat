@echo off
title n8n Search Proxy
echo ============================================
echo   n8n Search Proxy - Internet Gateway
echo ============================================
echo.
echo Starting on http://0.0.0.0:5100
echo API Docs: http://localhost:5100/docs
echo.

cd /d "%~dp0"
python server.py --port 5100

pause
