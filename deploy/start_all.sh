#!/usr/bin/env bash
# ============================================================
#  qbot 本地/服务器一键启动（Mac / Linux）
#  用法：bash deploy/start_all.sh
#  三个服务在后台运行，日志写到 logs/*.out，PID 写到 logs/*.pid
#  停止：bash deploy/stop_all.sh
# ============================================================
set -e
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
MARKET="${MARKET:-crypto}"
SYMBOLS="${SYMBOLS:-BTC/USDT ETH/USDT SOL/USDT}"
SYMBOL="${SYMBOL:-BTC/USDT}"
STRATEGY="${STRATEGY:-regime_switch}"
POLL="${POLL:-300}"

# 优先使用虚拟环境
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi

mkdir -p logs

start_one () {  # $1=名称 $2=pidfile $3...=命令
  local name="$1"; local pidfile="$2"; shift 2
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "· $name 已在运行 (PID $(cat "$pidfile"))，跳过"
    return
  fi
  nohup "$@" > "logs/${name}.out" 2>&1 &
  echo $! > "$pidfile"
  echo "✓ $name 已启动 (PID $!) → logs/${name}.out"
}

echo "启动 qbot 三个服务..."
start_one dashboard logs/dashboard.pid "$PY" run_dashboard.py
start_one watch     logs/watch.pid     "$PY" run_watch.py --market "$MARKET" --symbols $SYMBOLS --strategy "$STRATEGY" --poll "$POLL"
start_one live      logs/live.pid      "$PY" run_live.py --broker paper --market "$MARKET" --symbol "$SYMBOL" --strategy "$STRATEGY" --poll "$POLL" --notify

echo
echo "面板: http://127.0.0.1:8000"
echo "看日志: tail -f logs/watch.out"
echo "停止全部: bash deploy/stop_all.sh"
