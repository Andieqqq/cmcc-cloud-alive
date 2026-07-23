#!/usr/bin/env python3
"""Headless keepalive loop using cached token. Usage: python cmcc-headless-keepalive.py <user_service_id>"""
import sys, time, json
import cmcc_cloud_alive
from cmcc_cloud_alive import cloud, desktop_keepalive, token, core

def main():
    target = sys.argv[1]
    state_path = "/root/.cmcc-cloud-alive/state.json"
    interval = 300  # 5 min
    run_seconds = 0  # forever

    state = json.load(open(state_path))
    cached_user = state.get("username", "")
    print(f"[start] target={target} interval={interval}s user={cached_user}", flush=True)

    count = 0
    try:
        while True:
            count += 1
            tok = token.ensure_token(state_path, relogin=False)
            valid = tok[0] if isinstance(tok, (tuple, list)) else bool(tok)
            if not valid:
                print(f"[{core.short_time()}] #{count} token expired & no non-interactive relogin -> retry in {interval}s", flush=True)
                time.sleep(interval)
                continue
            print(f"[{core.short_time()}] #{count} keepalive start", flush=True)
            try:
                res = desktop_keepalive.once(
                    target, state_path,
                    send_probe=False, send_point=False,
                    send_disconnect_time=True, send_connect_events=False,
                    use_firm_auth=True,
                )
                ok = bool(res.get("candidateAccepted"))
                hb = (res.get("heartbeat") or {}).get("code", "-")
                info = (res.get("infoReport") or {}).get("code", "-")
                print(f"[{core.short_time()}] #{count} ok={ok} heartbeat={hb} info={info}", flush=True)
            except Exception as e:
                print(f"[{core.short_time()}] #{count} ERROR {e}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")

if __name__ == "__main__":
    main()
