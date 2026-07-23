#!/usr/bin/env python3
"""Login with a new account and create state files for keepalive."""
import sys, os, json
from cmcc_cloud_alive import core, auth

ACCOUNTS_DIR = "/root/.cmcc-cloud-alive/accounts"

def main():
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <account_name> <username> <password>")
        print(f"  Creates state files in {ACCOUNTS_DIR}/<account_name>/")
        sys.exit(1)

    account_name = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]

    account_dir = os.path.join(ACCOUNTS_DIR, account_name)
    os.makedirs(account_dir, exist_ok=True)

    state_path = os.path.join(account_dir, "state.json")
    print(f"[1/3] Logging in as {username} (state: {state_path})...")
    auth.password_login(username, password, state_path=state_path, save_password=True)

    print(f"[2/3] Fetching cloud list...")
    state = core.load_state(core.argparse.Namespace(state=state_path))
    clouds = core.list_clouds(core.argparse.Namespace(state=state_path),
                              state_override=state)
    state["cloudList"] = clouds
    state["lastCloudListAt"] = core.shanghai_now().isoformat()
    core.save_state(state, core.argparse.Namespace(state=state_path))

    print(f"[3/3] Creating per-VM state files...")
    for cloud in clouds:
        uid = str(cloud.get("userServiceId"))
        vm_state_path = os.path.join(account_dir, f"state-{uid}.json")
        vm_state = dict(state)
        vm_state["selectedUserServiceId"] = uid
        vm_state["selectedDesktop"] = cloud
        with open(vm_state_path, "w") as f:
            json.dump(vm_state, f, ensure_ascii=False, indent=2)
        os.chmod(vm_state_path, 0o600)

    print(f"\nAccount '{account_name}' set up with {len(clouds)} VM(s):")
    for cloud in clouds:
        uid = cloud.get("userServiceId")
        name = cloud.get("skuName", "")
        spec = cloud.get("skuSpec", "")
        status = cloud.get("vmStatusShow", "")
        svc = cloud.get("serviceStatus")
        print(f"  {uid}: {name} | {spec} | {status} | service={svc}")

    print(f"\nTo keepalive a VM from this account:")
    print(f"  python cmcc-boot-keepalive.py --account {account_name} <uid>")

if __name__ == "__main__":
    main()
