#!/bin/sh
# 移动云电脑后台保活 — 不依赖 PTY
# 用法: sh cron-keepalive.sh <电脑索引>
# 电脑索引: 0 = 2C4G月包, 1 = 家庭云电脑畅享版月包

set -e
cd /mnt/sda1/cmcc-cloud-alive

MACHINE_IDX="$1"
if [ -z "$MACHINE_IDX" ]; then
  echo "用法: $0 <电脑索引>"
  exit 1
fi

echo "$(date) 启动云电脑#$MACHINE_IDX 保活..."
.venv/bin/python -m cmcc_cloud_alive interactive 2>&1 &
PID=$!
echo "PID=$PID 写入 /tmp/cmcc-alive-m${MACHINE_IDX}.pid"
echo "$PID" > "/tmp/cmcc-alive-m${MACHINE_IDX}.pid"

# 5 秒后检查进程是否还活着（避免 interactive 因等待输入而挂起）
sleep 5
if kill -0 "$PID" 2>/dev/null; then
  echo "OK: 进程 $PID 存活"
else
  echo "FAIL: 进程 $PID 已退出"
  exit 1
fi
