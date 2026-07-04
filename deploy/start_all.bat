@echo off
REM ============================================================
REM  qbot one-click launcher (Windows)
REM  Double-click this file. It opens 4 windows:
REM    dashboard + 3 portfolio engines (paper / A-share / crypto)
REM  which power the panel tabs. Edit the "set" lines to tweak.
REM  NOTE: keep this file ASCII-only (Chinese comments break
REM        cmd.exe parsing on GBK-codepage systems).
REM ============================================================
cd /d "%~dp0.."

REM ---- edit these if you want ----
set STRATEGY=confluence
set TOP=60
set MAXPOS=12
set POLL=300

echo Starting qbot: dashboard + 3 portfolio engines ...

REM 1) Dashboard (panel at http://127.0.0.1:8000)
start "qbot dashboard (http://127.0.0.1:8000)" cmd /k python run_dashboard.py

REM 2) Paper portfolio (feeds the "Simulated" trading tab) - virtual money, safe
start "qbot portfolio PAPER" cmd /k python run_portfolio.py --mode paper --market crypto --strategy %STRATEGY% --top %TOP% --max-positions %MAXPOS% --poll %POLL% --notify

REM 3) A-share signals (feeds the "A-share live" tab) - signals only, no auto order
start "qbot portfolio A-SHARE (signals)" cmd /k python run_portfolio.py --mode ashare --market ashare --strategy %STRATEGY% --top 80 --signals-only --poll 900 --notify

REM 4) Crypto via OKX (feeds the "Crypto live" tab)
REM    Uses your .env OKX key. OKX_DEMO=1 => simulated (no real money). Set 0 for real.
start "qbot portfolio CRYPTO (OKX)" cmd /k python run_portfolio.py --mode crypto --market crypto --broker okx --strategy %STRATEGY% --top %TOP% --max-positions %MAXPOS% --poll %POLL% --notify

echo.
echo Done. Open http://127.0.0.1:8000 and check the 3 trading tabs.
echo Close a window to stop that service.
pause
