@echo off
REM ============================================================================
REM  VISTAS WATCHDOG  (deterministic backstop for the daily-refresh agent)
REM  Checks that prices are fresh + the agent actually ran. Writes
REM  data\_refresh\WATCHDOG_ALERT.txt (loud) + a Windows pop-up if not. No fixes,
REM  just detection. Schedule ~10:30pm, AFTER the 8pm agent. Independent of it.
REM ============================================================================
cd /d "%~dp0\.."
python pipeline\watchdog.py >> data\_refresh\watchdog.log 2>&1
