@echo off
REM ============================================================================
REM  Detect + cross-check (NSE bhavcopy) + repair suspicious stock price jumps.
REM   - interpolates 1-day bad ticks, back-adjusts unadjusted splits/bonuses,
REM     FLAGS (does not touch) ambiguous/real moves.
REM   - writes output\stock_data_quality_report.csv (every event, auditable)
REM   - writes data\Stocks Data PX Clean till <date>.csv
REM
REM  By default it does NOT overwrite the live snapshot. To PROMOTE the cleaned
REM  data to the canonical file (raw is backed up to data\_raw first), run:
REM        python -m vistas.clean_stocks --promote
REM  Offline (skip the NSE cross-check):  python -m vistas.clean_stocks --no-nse
REM ============================================================================
cd /d "%~dp0"
python -m vistas.clean_stocks %*
echo.
pause
