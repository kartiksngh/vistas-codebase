@echo off
REM ============================================================================
REM  DAILY REFRESH VISTAS  —  the one nightly job.
REM
REM  Refresh EVERY data source  ->  reload  ->  rebuild the terminal site  ->
REM  validate  ->  and (if the shell is valid) PUBLISH to
REM        https://kartiksngh.github.io/vistas/terminal/
REM
REM  Robust by design: one feed failing never aborts the run; NOTHING but a
REM  genuinely faulty shell blocks the publish (degraded feeds are just flagged).
REM  A dated health report is written to  data\_refresh\last_run.md .
REM
REM  FAILSAFE: if the auto-publish ever fails, the freshly built site is already
REM  on disk — just run  "Publish Last Build.bat"  to push it.
REM
REM  Optional flags (type after the command if running by hand):
REM     --no-push     build + validate only, do NOT push
REM     --light       skip the heavy feeds (bhavcopy, screener, issued shares)
REM     --skip a,b    skip named sources;   --only a,b   run only named sources
REM     --dry-run     refresh the data sources only (no rebuild / no publish)
REM ============================================================================
cd /d "%~dp0\.."
python -m vistas.pipeline %*
echo.
pause
