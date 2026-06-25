@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - pull Screener.in fundamentals (P^&L, ratios, valuation
echo   history, shareholding, peers) into a local cache.
echo.
echo   Needs your Screener login (or a cached session). Password is
echo   entered HIDDEN, used only for this run - never saved to disk.
echo.
echo   INCREMENTAL by default: only NEW + STALE companies are pulled.
echo     (no args)            = NIFTY 500 universe, incremental
echo     --universe all       = EVERY NSE-listed company (~2000, heavy)
echo     --universe watchlist = ~40 large/mid caps
echo     --full               = refetch everything (after corrections)
echo     TCS RELIANCE         = just these symbols
echo ================================================================
echo.
python "_refresh_screener.py" %*
echo.
pause
