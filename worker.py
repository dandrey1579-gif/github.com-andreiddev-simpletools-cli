import time, uuid, os
from datetime import datetime
from db import init_db, get_all_desired_state, save_operation

CHECK_INTERVAL = int(os.environ.get("SIMPLETOOLS_INTERVAL", "300"))

def check_resource(desired):
    if desired["provider"] == "github" and desired["resource_type"] == "Repository":
        import requests
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return None
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        try:
            resp = requests.get("https://api.github.com/user/repos", headers=headers, params={"per_page": 100})
            if resp.ok:
                repos = [r["name"] for r in resp.json()]
                if desired["spec"].get("name") not in repos:
                    op_id = f"drift-{uuid.uuid4().hex[:8]}"
                    manifest = {"kind": "github:Repository", "operation": "CREATE", "spec": desired["spec"], "reason": "DRIFT_DETECTED"}
                    save_operation(op_id, "PENDING", manifest)
                    return {"operation_id": op_id, "resource_id": desired["resource_id"], "action": "CREATE", "reason": "Ресурс отсутствует в реальности"}
        except:
            pass
    return None

def run_worker():
    init_db()
    print(f"Worker запущен. Интервал: {CHECK_INTERVAL} сек.")
    while True:
        try:
            for res in get_all_desired_state():
                result = check_resource(res)
                if result:
                    print(f"Дрейф: {result['resource_id']}")
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(e)
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run_worker()
