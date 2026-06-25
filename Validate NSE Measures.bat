@echo off
REM Double-click to validate the PR + valuation (P/E, P/B, Div-Yield) NSE endpoints
REM from THIS machine (~30 seconds, one index, a handful of requests). It prints the
REM exact columns NSE returns and writes output\_measures_probe.json.
cd /d "%~dp0"
echo Validating NSE PR + valuation endpoints (single index, ~30s)...
echo.
python "_validate_measures.py"
echo.
echo ----------------------------------------------------------------
echo Done. Leave this window open and tell Claude the result above,
echo or it can read output\_measures_probe.json directly.
echo ----------------------------------------------------------------
pause
