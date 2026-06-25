@echo off
REM ============================================================================
REM  PUBLISH LAST BUILD  —  push whatever site is already built on disk.
REM
REM  Skips the data pull AND the rebuild; validates the shell and (only if it is
REM  valid) pushes  output\terminal_site\  to
REM        https://kartiksngh.github.io/vistas/terminal/
REM
REM  Use this:
REM    - as the FAILSAFE when the nightly auto-publish fails (the build is already
REM      on disk, so this just retries the push), or
REM    - to publish a feature Claude just built into  output\terminal_site\ .
REM
REM  Seconds, not minutes (no fetch, no rebuild). A faulty shell is never published.
REM ============================================================================
cd /d "%~dp0\.."
python publish_terminal.py --no-rebuild %*
echo.
pause
