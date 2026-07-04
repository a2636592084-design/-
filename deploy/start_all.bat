@echo off
REM ============================================================
REM  qbot 本地一键启动（Windows）
REM  双击本文件，会打开三个窗口：面板 / 盯盘 / 模拟交易
REM  想改标的或策略，编辑下面的 set 变量即可
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0.."

REM ---- 可按需修改 ----
set MARKET=crypto
set SYMBOLS=BTC/USDT ETH/USDT SOL/USDT
set SYMBOL=BTC/USDT
set STRATEGY=regime_switch
set POLL=300

echo 正在启动 qbot 三个服务（面板 / 盯盘 / 模拟交易）...

start "qbot 面板 (http://127.0.0.1:8000)" cmd /k python run_dashboard.py

start "qbot 盯盘推送" cmd /k python run_watch.py --market %MARKET% --symbols %SYMBOLS% --strategy %STRATEGY% --poll %POLL%

start "qbot 模拟交易(paper)" cmd /k python run_live.py --broker paper --market %MARKET% --symbol %SYMBOL% --strategy %STRATEGY% --poll %POLL% --notify

echo.
echo 已启动。浏览器打开 http://127.0.0.1:8000 看面板。
echo 关闭对应的黑色窗口即可停止该服务。
pause
