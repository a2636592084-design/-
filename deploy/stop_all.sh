#!/usr/bin/env bash
# 停止 qbot 全部后台服务（Mac / Linux）
cd "$(dirname "$0")/.."
for svc in dashboard watch live; do
  pidfile="logs/${svc}.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "✓ 已停止 $svc (PID $pid)"
    else
      echo "· $svc 未在运行"
    fi
    rm -f "$pidfile"
  else
    echo "· 没找到 $svc 的 PID 文件"
  fi
done
