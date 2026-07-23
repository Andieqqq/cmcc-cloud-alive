#!/usr/bin/env python3
"""Aggressive boot + HTTP keepalive loop (NO CAG reconnect).

Strategy:
- CAG keepalive is DISABLED: it establishes a CAG session then drops it,
  which triggers platform to see "no active client" and shut down the VM.
  (Confirmed in code: cag_keepalive.run_loop() is disabled.)
- Use HTTP keepalive ONLY: heartbeat + infoReport + disconnectTime
  sent via the official HTTP API.  Platform accepts but does not prevent
  shutdown — but it does NOT actively trigger it either.
- 2-minute interval to catch shutdowns quickly.
- 5x retry on boot failure (immediate retry, not 5-min wait).

Safety: if uSmartView_VDI_Client is running locally, still works fine
(because we're NOT doing CAG reconnect, so no session replacement).
"""
import sys, time, json, fcntl, os, argparse
from cmcc_cloud_alive import cloud, cag_boot, desktop_keepalive, core, token

ACCOUNTS_DIR = "/root/.cmcc-cloud-alive/accounts"
STATE_DIR = "/root/.cmcc-cloud-alive"  # default account

BOOT_RETRIES = 5
BOOT_RETRY_INTERVAL = 8
KEEPALIVE_INTERVAL = 60  # 1 minute — 2h4g keeps going off despite HTTP keepalive
CAG_BOOT_WAIT = 60

# Parse --account flag
_parser = argparse.ArgumentParser()
_parser.add_argument("--account", help="Account name (uses accounts/<name>/ for state files)")
_args, _remaining = _parser.parse_known_args(sys.argv[1:])

if _args.account:
    STATE_DIR = os.path.join(ACCOUNTS_DIR, _args.account)
    if not os.path.isdir(STATE_DIR):
        print(f"[error] account directory not found: {STATE_DIR}", flush=True)
        sys.exit(1)
# Shift remaining args
sys.argv = [sys.argv[0]] + _remaining

def boot_with_retry(target, state_path, max_retries=BOOT_RETRIES):
    for attempt in range(1, max_retries + 1):
        # Use cag_boot.boot() directly — skip cloud.status() which fails when listClouds is down
        # cag_boot.boot() does not depend on listClouds
        try:
            # Pass None to use cached selectedUserServiceId from state (avoid listClouds)
            cag_boot.boot(None, state_path, boot_wait=CAG_BOOT_WAIT, timeout=25)
        except Exception as e:
            print(f"  [boot #{attempt}] boot() failed: {e}", flush=True)
            if attempt < max_retries:
                time.sleep(BOOT_RETRY_INTERVAL)
            continue
        # Verify status: try cloud.status first, fall back to HTTP keepalive probe
        try:
            s = cloud.status(None, state_path)
            running = cloud.is_running(s)
            if running:
                print(f"  [boot #{attempt}] done. running=True vmStatus={s.get('vmStatusShow')}", flush=True)
                return True
            print(f"  [boot #{attempt}] done. running=False vmStatus={s.get('vmStatus')}", flush=True)
        except Exception:
            # cloud.status failed (listClouds down) — use HTTP keepalive to probe
            probe = http_keepalive_once(target, state_path)
            if probe.get("accepted"):
                print(f"  [boot #{attempt}] done. running=True (via keepalive probe)", flush=True)
                return True
            print(f"  [boot #{attempt}] done. running=False vmStatus={probe.get('vmStatus')}", flush=True)
        if attempt < max_retries:
            time.sleep(BOOT_RETRY_INTERVAL)
    return False

def http_keepalive_once(target, state_path):
    """Send HTTP keepalive: heartbeat + infoReport + disconnectTime.
    Uses raw helper functions that take target directly (no listClouds dependency)."""
    try:
        hb = desktop_keepalive.heartbeat(target, state_path)
    except Exception as e:
        return {"error": f"heartbeat: {e}"}
    try:
        info = desktop_keepalive.info_report(state_path)
    except Exception as e:
        return {"error": f"infoReport: {e}"}
    try:
        dt = desktop_keepalive.disconnect_time(target, state_path)
    except Exception as e:
        dt = {"error": str(e)}

    hb_code = hb.get("code")
    info_code = info.get("code")
    hb_ok = hb_code in ({2000, 2001} | {4039, 4040, 4041, 4042})
    info_ok = info_code == 2000

    dt_str = None
    if dt and isinstance(dt, dict) and "error" not in dt:
        dt_str = str(dt)[:80]
    elif dt and not isinstance(dt, dict):
        dt_str = str(dt)[:80]

    return {
        "hb": hb_code, "hbMsg": hb.get("msg", ""),
        "info": info_code,
        "disconnectTime": dt_str,
        "accepted": bool(hb_ok and info_ok),
        "vmStatus": "?",  # unknown without listClouds
        "hbOk": hb_ok, "infoOk": info_ok,
    }

def main():
    target = sys.argv[1]
    state_path = os.path.join(STATE_DIR, f"state-{target}.json")
    interval = KEEPALIVE_INTERVAL

    print(f"[start] target={target} interval={interval}s "
          f"boot_retry={BOOT_RETRIES} boot_wait={CAG_BOOT_WAIT}", flush=True)
    print(f"[NOTE] CAG keepalive DISABLED (triggers platform auto-shutdown)", flush=True)

    # Ensure per-machine state file
    if not os.path.exists(state_path):
        global_state = os.path.join(STATE_DIR, "state.json")
        if os.path.exists(global_state):
            import shutil
            shutil.copy2(global_state, state_path)
        else:
            with open(state_path, "w") as f:
                json.dump({}, f)

    # Initial boot check — use cached state (None picks up selectedUserServiceId)
    try:
        s = cloud.status(None, state_path)
        if not cloud.is_running(s):
            print(f"[boot] VM is OFF ({s.get('vmStatusShow')}) -> booting...", flush=True)
            boot_with_retry(target, state_path)
        else:
            print(f"[boot] VM already running ({s.get('vmStatusShow')})", flush=True)
    except Exception as e:
        print(f"[boot] status check failed: {e}", flush=True)

    count = 0
    try:
        while True:
            count += 1
            # Check VM status
            try:
                s = cloud.status(None, state_path)
                vm_running = cloud.is_running(s)
                if not vm_running:
                    print(f"[{core.short_time()}] #{count} VM SHUTDOWN ({s.get('vmStatus')}) -> booting...",
                          flush=True)
                    if boot_with_retry(target, state_path):
                        print(f"[{core.short_time()}] #{count} BOOT SUCCESS", flush=True)
                    else:
                        print(f"[{core.short_time()}] #{count} BOOT FAILED after {BOOT_RETRIES} attempts",
                              flush=True)
                    time.sleep(interval)
                    continue
            except Exception as e:
                print(f"[{core.short_time()}] #{count} status check ERROR: {e} — continuing with keepalive anyway", flush=True)
                # Status check failed (e.g. platform 5000) — still send HTTP keepalive
                # HTTP keepalive doesn't depend on listClouds status
                r = http_keepalive_once(target, state_path)
                if r.get("error"):
                    print(f"[{core.short_time()}] #{count} HTTP keepalive ERROR: {r['error']}", flush=True)
                else:
                    # Detect shutdown from keepalive response
                    if not r.get("accepted") and r.get("vmStatus") == "已关机":
                        print(f"[{core.short_time()}] #{count} VM SHUTDOWN (via keepalive, listClouds down) -> booting...", flush=True)
                        if boot_with_retry(target, state_path):
                            print(f"[{core.short_time()}] #{count} BOOT SUCCESS", flush=True)
                        else:
                            print(f"[{core.short_time()}] #{count} BOOT FAILED", flush=True)
                        time.sleep(interval)
                        continue
                    print(f"[{core.short_time()}] #{count} HB={r.get('hb')} info={r.get('info')} "
                          f"accepted={r.get('accepted')} vmStatus={r.get('vmStatus')}", flush=True)
                time.sleep(interval)
                continue

            # VM running: HTTP keepalive
            print(f"[{core.short_time()}] #{count} HTTP keepalive start (VM={s.get('vmStatusShow')})", flush=True)
            r = http_keepalive_once(target, state_path)
            if r.get("error"):
                print(f"[{core.short_time()}] #{count} HTTP keepalive ERROR: {r['error']}", flush=True)
            else:
                dt_str = r.get("disconnectTime", "")
                # Detect shutdown from keepalive response itself
                if not r.get("accepted") and r.get("vmStatus") == "已关机":
                    print(f"[{core.short_time()}] #{count} VM SHUTDOWN detected via keepalive response -> booting...", flush=True)
                    if boot_with_retry(target, state_path):
                        print(f"[{core.short_time()}] #{count} BOOT SUCCESS", flush=True)
                    else:
                        print(f"[{core.short_time()}] #{count} BOOT FAILED after {BOOT_RETRIES} attempts", flush=True)
                    time.sleep(interval)
                    continue
                print(f"[{core.short_time()}] #{count} HB={r.get('hb')} "
                      f"info={r.get('info')} "
                      f"disconnectTime={dt_str} "
                      f"accepted={r.get('accepted')} "
                      f"vmStatus={r.get('vmStatus')}", flush=True)

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped", flush=True)

if __name__ == "__main__":
    main()
