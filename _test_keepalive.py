import sys, time, json
from cmcc_cloud_alive import cloud, desktop_keepalive, token, core
target = sys.argv[1]
state_path = "/root/.cmcc-cloud-alive/state.json"
tok = token.ensure_token(state_path, relogin=False)
valid = tok[0] if isinstance(tok, (tuple, list)) else bool(tok)
print(f"token valid={valid}", flush=True)
try:
    res = desktop_keepalive.once(target, state_path, send_probe=False, send_point=False,
        send_disconnect_time=True, send_connect_events=False, use_firm_auth=True)
    print(f"ok={bool(res.get('candidateAccepted'))} heartbeat={(res.get('heartbeat') or {}).get('code')} info={(res.get('infoReport') or {}).get('code')}", flush=True)
except Exception as e:
    print(f"ERROR {e}", flush=True)
