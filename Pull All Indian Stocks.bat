@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - pull ALL NSE-listed Indian stocks (~2000) from Yahoo
echo   (split/bonus/dividend-adjusted close, full history).
echo.
echo   Heavy + CHUNKED + RESUMABLE: it writes after every chunk, so
echo   leave it running; Ctrl-C any time and re-run to continue
echo   (symbols already held are skipped). Add --refetch to re-pull all.
echo ================================================================
echo.
python "_refresh_stocks.py" --all-nse %*
echo.
pause
