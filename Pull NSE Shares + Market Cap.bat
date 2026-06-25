@echo off
cd /d "%~dp0"
echo ================================================================
echo   Vistas - collect REAL issued-shares / market-cap (no estimation):
echo   AMFI bulk full market cap + SEBI large/mid/small cohort, plus
echo   NSE exact issuedSize per stock. market cap = our close x shares.
echo.
echo     (no args)     = AMFI refresh + issuedSize for the full universe
echo     --amfi-only   = only the AMFI bulk market-cap / cohort file
echo     --issued-only = only the NSE exact issued-share pull
echo     --limit 600   = issuedSize for the top-600 names by market cap
echo     --new-only    = issuedSize only for names not already cached
echo     --list        = show what is cached, then exit
echo     --validate    = rank-check our market cap vs Bloomberg (local)
echo.
echo   NSE issuedSize sits behind the exchange WAF - run this on the
echo   same machine/network that serves the terminal (datacenter / VPN
echo   IPs get blocked). AMFI is a plain download and always works.
echo ================================================================
echo.
python "_refresh_shares.py" %*
echo.
pause
