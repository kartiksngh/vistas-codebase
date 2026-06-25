@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - Focused PR + Valuation (P/E, P/B, Div Yield) backfill
echo.
echo   TIP: run this on your phone HOTSPOT (a fresh IP) to sidestep
echo   NSE's rate-limit on these endpoints. It writes SEPARATE files;
echo   your TR snapshot is never touched. Safe to stop anytime.
echo ================================================================
echo.
python "_backfill_measures.py" %*
echo.
echo ----------------------------------------------------------------
echo Done. Tell Claude the RESULT above, or run with --all later for
echo the full index universe.
echo ----------------------------------------------------------------
pause
