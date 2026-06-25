@echo off
title Refresh Vistas Passive
cd /d "%~dp0"
echo Refreshing NSE data, rebuilding the deck, validating, and publishing to GitHub...
echo (A faulty deck will NOT be published - the live link keeps the last good one.)
echo.
python "publish_passive.py" %*
echo.
echo ============================================================
echo  Finished. Read the messages above.
echo  Live link: https://kartiksngh.github.io/vistas/passive/
echo ============================================================
pause
