@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - pull Screener fundamentals for EVERY NSE-listed
echo   company (~2000), not just the NIFTY 500.
echo.
echo   AUTO-RESUMES across the per-run cap until done; INCREMENTAL,
echo   so the ~500 already cached are skipped and it continues with
echo   the rest. Heavy (leave it running many hours); Ctrl-C and
echo   re-launch any time to continue. Add --full to refetch all.
echo.
echo   Note: companies not covered by Screener are skipped cleanly,
echo   so the final count may be a bit under 2000.
echo ================================================================
echo.
python "_refresh_screener.py" --universe all %*
echo.
pause
