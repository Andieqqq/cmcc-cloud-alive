#!/bin/sh
# 移动云电脑保活 — tmux 后台模式
# 用法: sh cmcc-keepalive.sh <电脑索引>  [启动|重启|停止|状态]
# 电脑索引: 0 = 2C4G月包, 1 = 家庭云电脑畅享版月包

set -e
cd /mnt/sda1/cmcc-cloud-alive

MACHINE="$1"
ACTION="${2:-启动}"

if [ -z "$MACHINE" ] || ! echo "$MACHINE" | grep -qE "^[0-9]+$"; then
  echo "用法: $0 <电脑索引> [启动|重启|停止|状态]"
  echo "  电脑索引: 0=2C4G月包  1=家庭云电脑畅享版月包"
  exit 1
fi

SESSION="cmcc-alive-m${MACHINE}"

case "$ACTION" in
  启动)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "会话 $SESSION 已存在（PID $(tmux display-message -p '#{pane_pid}' -t "$SESSION")）"
      exit 0
    fi
    tmux new-session -d -s "$SESSION" -x 120 -y 40
    tmux send-keys -t "$SESSION" 'cd /mnt/sda1/cmcc-cloud-alive && .venv/bin/python -m cmcc_cloud_alive interactive' Enter
    sleep 8
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      PID=$(tmux display-message -p '#{pane_pid}' -t "$SESSION")
      echo "OK: $SESSION 已启动 (PID $PID)"
    else
      echo "FAIL: $SESSION 启动失败"
      exit 1
    fi
    ;;
  重启)
    $0 "$MACHINE" 停止
    sleep 2
    $0 "$MACHINE" 启动
    ;;
  停止)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux kill-session -t "$SESSION"
      echo "已停止 $SESSION"
    else
      echo "$SESSION 不存在"
    fi
    ;;
  状态)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      PID=$(tmux display-message -p '#{pane_pid}' -t "$SESSION")
      UPTIME=$(tmux display-message -p '#{session_attached}' -t "$SESSION")
      echo "$SESSION 运行中 (PID $PID)"
      # 检查 python 进程是否活着
      if ps -w | grep -v grep | grep -q "cmcc_cloud_alive interactive"; then
        echo "  python 进程: 存活"
      else
        echo "  python 进程: 已退出！需要重启"
      fi
    else
      echo "$SESSION 未运行"
    fi
    ;;
  *)
    echo "未知操作: $ACTION"
    echo "支持: 启动|重启|停止|状态"
    exit 1
    ;;
esac
