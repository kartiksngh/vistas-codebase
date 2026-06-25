@echo off
REM ============================================================================
REM  NIGHTLY BUILD (NO PUBLISH)  —  the failsafe half of the pipeline.
REM
REM  Refresh every source  ->  rebuild the terminal site  ->  validate, but
REM  DO NOT push. Use this when you'd rather eyeball the health report
REM  (data\_refresh\last_run.md) first and publish by hand afterwards with
REM  "Publish Last Build.bat".
REM
REM  Same engine as Daily Refresh, just with --no-push.
REM ============================================================================
cd /d "%~dp0\.."
python -m vistas.pipeline --no-push %*
echo.
pause
