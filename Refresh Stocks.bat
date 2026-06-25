@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - refresh stock prices (Yahoo Finance, adjusted close)
echo.
echo   no args   = large/mid-cap watchlist (~40), full history
echo   --all     = full current NIFTY 500
echo   --update  = just today's tail for held symbols
echo ================================================================
echo.
python "_refresh_stocks.py" %*
echo.
pause
