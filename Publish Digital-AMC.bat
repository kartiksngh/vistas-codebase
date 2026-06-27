@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas Digital-AMC - MARK + REBUILD + PUBLISH (one click)
echo.
echo   Marks the 4 pilot books to the latest close, rebuilds the
echo   digital-AMC site, copies it into _pages\digital-amc and pushes
echo   to GitHub Pages, then runs the standing off-machine backups
echo   (source -^> vistas-codebase; licensed ARM -^> encrypted mirror).
echo.
echo   Use this AFTER a monthly LLM round (run by Claude), or any time
echo   to push the freshly daily-marked NAV. Gated by the same single-
echo   flight build lock as the terminal (never two builds at once).
echo.
echo   Live: https://kartiksngh.github.io/vistas/digital-amc/
echo ================================================================
echo.
python -m vistas.amc_round publish %*
echo.
pause
