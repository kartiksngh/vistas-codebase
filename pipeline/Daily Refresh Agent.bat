@echo off
REM ============================================================================
REM  VISTAS DAILY DATA-REFRESH AGENT  (Supervised)
REM
REM  Headless Claude reads pipeline\DAILY_REFRESH_AGENT.md and runs the adaptive
REM  daily loop:  refresh -> build -> validate -> diagnose & REPAIR degraded feeds
REM  (DATA actions only; it FLAGS any needed code change to
REM  data\_refresh\NEEDS_REVIEW.md instead of editing) -> PUBLISH only clean data
REM  -> LOG to data\_refresh\agent_journal.md (so the system compounds).
REM
REM  This is the ADAPTIVE wrapper around the deterministic pipeline.py: the script
REM  handles the 90% normal path; the agent handles NSE's probabilistic curveballs.
REM
REM  Scheduled daily via Task Scheduler ("Vistas Daily Refresh"). Full transcript
REM  -> data\_refresh\agent_run.log ; the agent's own diary -> agent_journal.md.
REM ============================================================================
cd /d "%~dp0\.."
REM claude.exe lives in the user-local bin, which is NOT on the Task Scheduler / cmd PATH -> use full path.
set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
echo.>> data\_refresh\agent_run.log
echo ===== AGENT RUN %DATE% %TIME% =====>> data\_refresh\agent_run.log
"%CLAUDE%" -p "Run today's Vistas daily data refresh. Read and FOLLOW your standard operating procedure at pipeline\DAILY_REFRESH_AGENT.md exactly, end to end, then stop. You are in SUPERVISED mode: fix data-pull problems yourself (retry/wait/re-pace), publish ONLY clean validated data, log everything to data\_refresh\agent_journal.md, but NEVER edit code - write any needed code change as a proposal to data\_refresh\NEEDS_REVIEW.md and move on." --model claude-opus-4-8 --dangerously-skip-permissions --disallowedTools "Edit" "NotebookEdit" >> data\_refresh\agent_run.log 2>&1
echo ===== AGENT DONE %DATE% %TIME% =====>> data\_refresh\agent_run.log
