@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - preview the HOSTED hybrid terminal locally.
echo.
echo   The hosted site loads stock/company data on demand, so it must
echo   be SERVED (a double-clicked file can't fetch). This starts a
echo   tiny local web server and opens it in your browser.
echo.
echo   Leave this window open while browsing; close it to stop.
echo ================================================================
echo.
start "" http://127.0.0.1:8799/
python -m http.server 8799 --directory "output/terminal_site" --bind 127.0.0.1
