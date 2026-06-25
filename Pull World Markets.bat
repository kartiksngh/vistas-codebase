@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - pull WORLD / cross-asset prices from Yahoo (no key):
echo   global equity indices, commodities, FX, bond yields,
echo   credit/rate ETFs, volatility and crypto.
echo.
echo     (no args)   = full catalog, full history (merges into snapshot)
echo     --update    = append only today's new tail
echo     --list      = just print the catalog (no fetch)
echo ================================================================
echo.
python "_refresh_world.py" %*
echo.
pause
