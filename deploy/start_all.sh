#!/usr/bin/env bash
# ============================================================
#  qbot 一键启动（Mac / Linux / 云服务器）
#  用法：bash deploy/start_all.sh
#  启动：面板 + 3个组合引擎(模拟/A股信号/加密OKX)，喂给面板三个交易页
#  日志 logs/*.out，PID logs/*.pid；停止：bash deploy/stop_all.sh
# ============================================================
set -e
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"
STRATEGY="${STRATEGY:-confluence}"
TOP="${TOP:-60}"
MAXPOS="${MAXPOS:-12}"
POLL="${POLL:-300}"
mkdir -p logs

start_one () {  # $1=名称 $2=pidfile $3...=命令
  local name="$1"; local pidfile="$2"; shift 2
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "· $name 已在运行 (PID $(cat "$pidfile"))，跳过"; return
  fi
  nohup "$@" > "logs/${name}.out" 2>&1 &
  echo $! > "$pidfile"
  echo "✓ $name 已启动 (PID $!) → logs/${name}.out"
}

echo "启动 qbot：面板 + 3个组合引擎..."
start_one dashboard logs/dashboard.pid "$PY" run_dashboard.py
start_one pf_paper  logs/pf_paper.pid  "$PY" run_portfolio.py --mode paper --market crypto \
  --strategy "$STRATEGY" --top "$TOP" --max-positions "$MAXPOS" --poll "$POLL" --notify
start_one pf_ashare logs/pf_ashare.pid "$PY" run_portfolio.py --mode ashare --market ashare \
  --strategy "$STRATEGY" --top 80 --signals-only --poll 900 --notify
# 加密走 OKX（用 .env 的 key；OKX_DEMO=1 为模拟盘，无真钱）。无 key 时该进程会自行退出。
start_one pf_crypto logs/pf_crypto.pid "$PY" run_portfolio.py --mode crypto --market crypto \
  --broker okx --trade-type swap --leverage 3 --allow-short \
  --strategy "$STRATEGY" --top "$TOP" --max-positions "$MAXPOS" --poll "$POLL" --notify

echo
echo "面板: http://127.0.0.1:8000  （回测/扫描 + 三个交易页）"
echo "看日志: tail -f logs/pf_paper.out"
echo "停止全部: bash deploy/stop_all.sh"
