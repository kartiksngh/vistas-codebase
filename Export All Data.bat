@echo off
REM ============================================================================
REM  Dump ALL Vistas data to fast, portable files under .\export
REM    export\parquet\  -> pandas: pd.read_parquet("export/parquet/<name>.parquet")
REM    export\excel\    -> open the .xlsx workbooks
REM  Re-run any time after a data refresh to regenerate the dump.
REM ============================================================================
cd /d "%~dp0"
python _export_all.py
echo.
pause
