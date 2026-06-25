@echo off
REM ============================================================================
REM  Refresh + publish the Vistas TERMINAL v2 deck (TR/PR + valuation).
REM  Double-click this once NSE has cooled down to populate the valuation/PR data
REM  and (optionally) publish to  https://kartiksngh.github.io/vistas/terminal/
REM
REM  It does: incremental NSE pull (all measures) -> rebuild v2 deck -> validate
REM           -> publish to the terminal/ path (v1 passive deck is untouched).
REM
REM  If NSE is still throttling, the pull just degrades and it rebuilds from the
REM  data already on disk. To skip the NSE pull entirely, run:
REM        python publish_terminal.py --no-fetch
REM  To build + validate WITHOUT publishing:   python publish_terminal.py --no-push
REM  To also email the deck (after setting VISTAS_SMTP_USER/PASS):  add  --email
REM ============================================================================
cd /d "%~dp0"
python publish_terminal.py %*
echo.
pause
