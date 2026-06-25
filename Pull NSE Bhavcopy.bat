@echo off
REM ============================================================================
REM  Rebuild the Vistas STOCK panel from the NSE BHAVCOPY (the exchange's own
REM  daily close) - the decimal-accurate replacement for yfinance.
REM
REM    1. fetch + cache NSE corporate actions (splits / bonuses / dividends)
REM    2. fetch + cache every NSE trading day's bhavcopy zip (resumable, polite)
REM    3. assemble raw closes by ISIN, build the security master (PERMID lineage),
REM       apply the verified corporate actions, reconstruct the TOTAL-RETURN level,
REM       NSE-align, and write data\Stocks Data TR till <date>.csv (goes live).
REM
REM  Safe to re-run: cached days/years are skipped. The first full run from 2000
REM  fetches ~6500 days and can take a while - leave it running.
REM
REM    (no args)      = full pipeline from 2000, switch the app to the bhavcopy panel
REM    --from 2018    = start the price history at 2018 (validate recent years first)
REM    --no-fetch     = skip the network; rebuild from the existing cache
REM    --no-promote   = write to output\ only (don't switch the live app over)
REM ============================================================================
cd /d "%~dp0"
python "_refresh_bhav.py" %*
echo.
pause
