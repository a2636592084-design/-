@echo off
REM ============================================================
REM  qbot one-click launcher (Windows)
REM  Double-click this file. It opens 3 windows:
REM    dashboard / signal watcher / paper trading
REM  Edit the "set" lines below to change symbols or strategy.
REM  NOTE: keep this file ASCII-only. Chinese comments break
REM        cmd.exe parsing on GBK-codepage systems.
REM ============================================================
cd /d "%~dp0.."

REM ---- edit these if you want ----
set MARKET=crypto
set SYMBOLS=BTC/USDT ETH/USDT SOL/USDT
set SYMBOL=BTC/USDT
set STRATEGY=regime_switch
set POLL=300

echo Starting qbot services: dashboard / watcher / paper-trading ...

start "qbot dashboard (http://127.0.0.1:8000)" cmd /k python run_dashboard.py

start "qbot signal watcher" cmd /k python run_watch.py --market %MARKET% --symbols %SYMBOLS% --strategy %STRATEGY% --poll %POLL%

start "qbot paper trading" cmd /k python run_live.py --broker paper --market %MARKET% --symbol %SYMBOL% --strategy %STRATEGY% --poll %POLL% --notify

echo.
echo Done. Open http://127.0.0.1:8000 in your browser.
echo Close a window to stop that service.
pause
