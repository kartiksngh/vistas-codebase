@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas Digital-AMC - DAILY MARK (no LLM, paper-only)
echo.
echo   Re-prices the 4 live pilot books to the latest close every
echo   trading day, so the paper NAV is a true daily total-return
echo   track and each day gets its CITI daily fact sheet.
echo.
echo     * NO trades, NO LLM, NO look-ahead (prices on/before today).
echo     * Idempotent + gap-filling - re-marks the whole window each
echo       run, so a missed day self-heals; book.json is NEVER changed.
echo.
echo   This is the autonomous heartbeat BETWEEN the monthly LLM
echo   rebalance rounds. Schedule it nightly (Task Scheduler) AFTER
echo   the data refresh. Writes:
echo     output\_amc\live\nav\<scheme>.csv  + daily_mark_status.json
echo     amc_book\<AMC>\<scheme>\daily\<YYYY-MM>.json (the fact sheets)
echo ================================================================
echo.
python -m vistas.amc_daily_mark
echo.
pause
