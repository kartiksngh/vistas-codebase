@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - pull INDIA-NATIVE MACRO from official free sources:
echo   CPI/WPI inflation, RBI rates ^& G-sec yields, money ^& credit,
echo   forex reserves, external trade, IIP, and FII/DII flows.
echo.
echo     (no args)   = fetch every wired series, merge into snapshot
echo     --list      = print the catalog (id/name/source/status)
echo     --probe     = quick reachability check of live sources
echo.
echo   Optional: set DATA_GOV_API_KEY (free at data.gov.in) to lift
echo   the data.gov.in sample-key rate limit. Network is optional -
echo   a source that is down is skipped; history is preserved.
echo ================================================================
echo.
python "_refresh_macro.py" %*
echo.
pause
