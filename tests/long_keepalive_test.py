#!/usr/bin/env python3
"""全链路保活长测 — 一键跑。40/60/120分钟三节点验收。

用法：
  python tests/long_keepalive_test.py

凭据从环境变量 CMCC_USERNAME/CMCC_PASSWORD 读取，自动跑 120 分钟。
每 5 分钟发 60 秒保活流量，每分钟检测是否关机。
40min / 60min / 120min 三节点全过 → 合格。
"""

import datetime as dt
import json
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path

# ── 凭据从环境变量读取（绝不落盘明文） ───────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = str(REPO_ROOT / "bin" / "cmcc_cloud_alive.py")
USERNAME = os.environ.get("CMCC_USERNAME", "")
PASSWORD = os.environ.get("CMCC_PASSWORD", "")
STATE_PATH = str(Path(tempfile.gettempdir()) / "long_keepalive_state.json")

KEEPALIVE_INTERVAL = 300    # 5分钟
KEEPALIVE_BURST = 60        # 每次60秒流量
POWER_INTERVAL = 60         # 每分钟检测
MILESTONES = [40, 60, 120]  # 三个验收节点（分钟）
TOTAL_MIN = 120

# BBS 上报（如果可用）
BBS_URL = "http://127.0.0.1:5761"
BBS_KEY = os.environ.get("BBS_API_KEY", "")
BBS_TOKEN = os.environ.get("BBS_MASTER_TOKEN", "")


def log(msg, tag="INFO"):
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)


def bbs_post(content, title=None):
    """尝试把消息推送到 BBS（失败不影响主流程）。"""
    try:
        import requests
        payload = {"token": BBS_TOKEN, "content": content}
        if title:
            payload["title"] = title
        requests.post(f"{BBS_URL}/post", json=payload,
                       headers={"X-API-Key": BBS_KEY}, timeout=8)
    except Exception:
        pass  # BBS 不可用不阻塞


def run_cli(args_list, timeout=None):
    """调用 cmcc_cloud_alive CLI。"""
    cmd = [sys.executable, CLI, "--state", STATE_PATH] + [str(a) for a in args_list]
    log(f"$ {' '.join(cmd)}", "CMD")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=str(REPO_ROOT))
        if r.stdout:
            print(r.stdout[-1500:], flush=True)
        if r.returncode != 0 and r.stderr:
            print(r.stderr[-500:], flush=True)
        return r
    except subprocess.TimeoutExpired:
        log("命令超时", "WARN")
        return None


def parse_usid(out):
    """从 list 输出中提取 userServiceId。"""
    try:
        data = json.loads(out)
    except Exception:
        import re
        m = re.search(r'"userServiceId"\s*:\s*(\d+)', out)
        return int(m.group(1)) if m else None
    items = data if isinstance(data, list) else data.get("list", data.get("items", []))
    for it in items:
        uid = it.get("userServiceId") or it.get("id")
        if uid:
            return int(uid)
    return None


def check_power(usid):
    """检测云电脑是否 running。"""
    r = run_cli(["power-monitor", str(usid),
                 "--interval", "1", "--duration", "1",
                 "--no-fail-on-off", "--continue-on-off"],
                timeout=30)
    if r is None:
        return False
    out = r.stdout + r.stderr
    import re
    m = re.search(r'"status"\s*:\s*"(\w+)"', out)
    return m and m.group(1) == "running"


# ═══════════════════════════════════════════════════════
def main():
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║      全链路保活长测 — 一键运行              ║")
    print("║  自动登录 → 自动保活 → 自动检测            ║")
    print("║  40/60/120 分钟三节点验收                   ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    # ── 凭据守卫：无环境变量则 skip，绝不崩溃 ──
    if not USERNAME or not PASSWORD:
        log("缺少环境变量 CMCC_USERNAME / CMCC_PASSWORD，无法运行长测。", "ERROR")
        log("请先设置：export CMCC_USERNAME=xxx; export CMCC_PASSWORD=xxx", "ERROR")
        sys.exit(2)

    log(f"账号: {USERNAME}")
    log(f"保活: 每 {KEEPALIVE_INTERVAL}秒 发 {KEEPALIVE_BURST}秒 屏幕流量")
    log(f"检测: 每 {POWER_INTERVAL}秒 检查是否关机")
    log(f"节点: {MILESTONES} 分钟, 全过才合格")
    log(f"总时长: {TOTAL_MIN} 分钟")
    print()

    # ── 1. 登录 ──
    log(">>> [1/3] 登录云电脑…")
    run_cli(["login", USERNAME, PASSWORD, "--save-password"])

    # ── 2. 获取桌面列表 ──
    log(">>> [2/3] 获取桌面列表…")
    r = run_cli(["list"], timeout=60)
    usid = parse_usid(r.stdout) if r and r.stdout else None
    if not usid:
        log("【失败】无法自动获取桌面 ID", "ERROR")
        bbs_post(f"【长测失败】{USERNAME} 无法获取桌面 ID")
        sys.exit(1)
    log(f"桌面 ID: {usid}")
    run_cli(["select", str(usid)])

    bbs_post(f"【长测启动】账号={USERNAME} 桌面ID={usid} 总时长={TOTAL_MIN}min "
             f"节点={MILESTONES}min")

    # ── 3. 主循环 ──
    log(">>> [3/3] 进入保活+检测循环（120分钟）…")
    print()

    milestone_results = {m: None for m in MILESTONES}
    t0 = time.time()
    next_power = 0.0
    next_keepalive = 0.0

    while True:
        elapsed = time.time() - t0
        if elapsed >= TOTAL_MIN * 60:
            break
        elapsed_min = elapsed / 60.0

        # ---- 保活触发 ----
        if elapsed >= next_keepalive:
            log(f"━━━ [{elapsed_min:.0f}min] 保活中: 发送 {KEEPALIVE_BURST}秒屏幕流量 ━━━")
            run_cli(["keepalive", str(usid),
                     "--interval", str(KEEPALIVE_INTERVAL),
                     "--run-seconds", str(KEEPALIVE_BURST),
                     "--probe", "--point"],
                    timeout=KEEPALIVE_BURST + 90)
            next_keepalive = elapsed + KEEPALIVE_INTERVAL
            continue

        # ---- 关机检测 ----
        if elapsed >= next_power:
            running = check_power(usid)
            status = "✅ 运行中" if running else "❌ 已关机/离线"
            log(f"[{elapsed_min:.0f}min] 电源状态: {status}")
            if not running:
                for m in MILESTONES:
                    if elapsed_min >= m and milestone_results[m] is None:
                        milestone_results[m] = f"FAIL(关机@ {elapsed_min:.0f}min)"
            next_power = elapsed + POWER_INTERVAL

        # ---- 节点判定 ----
        for m in MILESTONES:
            if milestone_results[m] is None and elapsed_min >= m:
                running = check_power(usid)
                milestone_results[m] = "PASS" if running else f"FAIL(关机@ {elapsed_min:.0f}min)"
                log(f"◆◆◆ 节点 {m}min: {milestone_results[m]} ◆◆◆", "MILE")
                bbs_post(f"【长测节点 {m}min】{milestone_results[m]}")

        time.sleep(5)

    # ── 4. 汇总报告 ──
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║              测试报告                        ║")
    print("╚══════════════════════════════════════════════╝")
    all_pass = True
    for m in MILESTONES:
        res = milestone_results.get(m, "未到达")
        ok = res and res.startswith("PASS")
        all_pass = all_pass and ok
        icon = "✅" if ok else "❌"
        print(f"  节点 {m:>3}分钟 : {res}  {icon}")
    verdict = "合格 (QUALIFIED)" if all_pass else "不合格 (FAILED)"
    print(f"  ─────────────────────────────")
    print(f"  最终判定: {verdict}")
    print()

    log(f"判定: {verdict}", "VERDICT")
    bbs_post(f"【长测完成】账号={USERNAME} 桌面ID={usid} "
             f"节点结果={milestone_results} 判定={verdict}",
             title=f"长测报告: {verdict}")

    # 写本地报告
    report = {
        "username": USERNAME,
        "userServiceId": usid,
        "totalMinutes": TOTAL_MIN,
        "milestones": milestone_results,
        "verdict": verdict,
        "timestamp": dt.datetime.now().isoformat(),
    }
    report_path = Path(tempfile.gettempdir()) / "long_keepalive_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    log(f"报告已保存: {report_path}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
