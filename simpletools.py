#!/usr/bin/env python3
"""
simpletools CLI v1.0 – Управление инфраструктурой через текстовые команды.
Модель: WISH → SEE → SAY
Провайдеры: GitHub, AWS S3

Все модули (db, policy_engine, worker, webui) встроены в этот файл.
"""

import sys
import os
import json
import uuid
import re
import sqlite3
import time
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for

# ===================== ВСТРОЕННЫЙ МОДУЛЬ db =====================
DB_PATH = os.environ.get("SIMPLETOOLS_DB", "simpletools.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    status TEXT DEFAULT 'PENDING',
                    manifest TEXT,
                    result TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS desired_state (
                    resource_id TEXT PRIMARY KEY,
                    provider TEXT,
                    resource_type TEXT,
                    spec TEXT,
                    labels TEXT DEFAULT '{}',
                    created_at TEXT,
                    updated_at TEXT
                )''')
    conn.commit()
    conn.close()

def save_operation(oid, status, manifest=None, result=None):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("INSERT OR REPLACE INTO operations (id, status, manifest, result, created_at, updated_at) VALUES (?,?,?,?,?,?)",
              (oid, status, json.dumps(manifest), json.dumps(result), now, now))
    conn.commit()
    conn.close()

def get_operation(oid):
    conn = get_connection()
    row = conn.execute("SELECT * FROM operations WHERE id=?", (oid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def list_operations(limit=20):
    conn = get_connection()
    rows = conn.execute("SELECT id, status, created_at FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_desired_state(rid, provider, rtype, spec, labels=None):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("INSERT OR REPLACE INTO desired_state (resource_id, provider, resource_type, spec, labels, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
              (rid, provider, rtype, json.dumps(spec), json.dumps(labels or {}), now, now))
    conn.commit()
    conn.close()

def get_all_desired_state():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM desired_state").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_desired_state(rid):
    conn = get_connection()
    conn.execute("DELETE FROM desired_state WHERE resource_id=?", (rid,))
    conn.commit()
    conn.close()

# ===================== ВСТРОЕННЫЙ POLICY ENGINE =====================
import yaml

POLICIES_FILE = os.environ.get("SIMPLETOOLS_POLICIES", "policies.yaml")

def load_policies():
    try:
        with open(POLICIES_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {"rules": []}

def check_policies(manifest):
    policies = load_policies()
    result = {"allowed": True, "warnings": [], "errors": []}
    for rule in policies.get("rules", []):
        cond = rule.get("condition", {})
        match = True
        if "action" in cond and manifest.get("operation", "").upper() != cond["action"].upper():
            match = False
        if "labels" in cond:
            mlabels = manifest.get("spec", {}).get("labels", {})
            for k, v in cond["labels"].items():
                if mlabels.get(k) != v:
                    match = False
        if match:
            effect = rule.get("effect", "WARN")
            msg = rule.get("message", "Нарушение политики")
            if effect == "DENY":
                result["allowed"] = False
                result["errors"].append(msg)
            elif effect == "WARN":
                result["warnings"].append(msg)
    return result

# ===================== КОНФИГУРАЦИЯ =====================
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")

# ===================== WISH / SEE / EXECUTE =====================
def parse_wish(text):
    text = text.lower().strip()
    if "github" in text and ("репозиторий" in text or "repo" in text):
        provider, action = "github", "create_repo"
    elif "github" in text and "удали" in text:
        provider, action = "github", "delete_repo"
    elif "s3" in text and ("бакет" in text or "bucket" in text):
        provider, action = "aws", "create_bucket"
    elif "s3" in text and "удали" in text:
        provider, action = "aws", "delete_bucket"
    else:
        return {"error": "Не могу понять задание. Поддерживается: GitHub репозиторий, AWS S3 бакет."}

    name_match = re.search(r'(?:репозиторий|repo|бакет|bucket)\s+["\']?([\w\-\.]+)["\']?', text)
    name = name_match.group(1) if name_match else None
    if not name:
        return {"error": "Не указано имя ресурса."}

    private = "приват" in text or "private" in text
    desc_match = re.search(r'(?:описание|description)\s+["\']?(.+?)["\']?(?:\s|$)', text)
    description = desc_match.group(1) if desc_match else ""
    region_match = re.search(r'(?:регион|region)\s+["\']?([\w\-]+)["\']?', text)
    region = region_match.group(1) if region_match else AWS_REGION

    labels = {}
    if "prod" in text:
        labels["env"] = "prod"
    elif "dev" in text:
        labels["env"] = "dev"
    elif "staging" in text:
        labels["env"] = "staging"

    return {
        "provider": provider,
        "action": action,
        "name": name,
        "private": private,
        "description": description,
        "region": region,
        "labels": labels
    }

def formalize(parsed):
    a = parsed["action"]
    if a == "create_repo":
        return {"kind": "github:Repository", "operation": "CREATE", "spec": {"name": parsed["name"], "private": parsed["private"], "description": parsed["description"], "labels": parsed.get("labels", {})}}
    if a == "delete_repo":
        return {"kind": "github:Repository", "operation": "DELETE", "spec": {"name": parsed["name"]}}
    if a == "create_bucket":
        return {"kind": "aws:S3:Bucket", "operation": "CREATE", "spec": {"name": parsed["name"], "region": parsed["region"], "private": parsed["private"]}}
    if a == "delete_bucket":
        return {"kind": "aws:S3:Bucket", "operation": "DELETE", "spec": {"name": parsed["name"]}}
    return {}

def calculate_plan(manifest):
    op, kind, name = manifest["operation"], manifest["kind"], manifest["spec"]["name"]
    if kind == "github:Repository":
        if op == "CREATE":
            return {"summary": f"Создать GitHub-репозиторий '{name}'", "provider": "GitHub", "actions": ["POST /user/repos"], "risk": "Низкий", "cost": "Бесплатно", "reversible": True, "estimated_time": "~2 сек"}
        return {"summary": f"Удалить GitHub-репозиторий '{name}'", "provider": "GitHub", "actions": [f"DELETE /repos/{{owner}}/{name}"], "risk": "⚠️ ВЫСОКИЙ", "cost": "Бесплатно", "reversible": False, "estimated_time": "~1 сек"}
    if kind == "aws:S3:Bucket":
        region = manifest["spec"].get("region", AWS_REGION)
        if op == "CREATE":
            return {"summary": f"Создать S3 бакет '{name}' в {region}", "provider": "AWS S3", "actions": [f"create_bucket(Bucket='{name}')"], "risk": "Низкий", "cost": "~$0.023/ГБ/мес", "reversible": True, "estimated_time": "~3 сек"}
        return {"summary": f"Удалить S3 бакет '{name}'", "provider": "AWS S3", "actions": [f"delete_bucket(Bucket='{name}')"], "risk": "⚠️ ВЫСОКИЙ", "cost": "Бесплатно", "reversible": False, "estimated_time": "~2 сек"}
    return {}

def execute(manifest):
    kind, op, name = manifest["kind"], manifest["operation"], manifest["spec"]["name"]
    if kind == "github:Repository":
        if not GITHUB_TOKEN:
            return {"status": "FAILED", "error": "GITHUB_TOKEN не задан."}
        import requests
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        if op == "CREATE":
            check = requests.get("https://api.github.com/user/repos", headers=headers, params={"per_page": 100})
            if check.ok and name in [r["name"] for r in check.json()]:
                return {"status": "NOOP", "message": f"Репозиторий '{name}' уже существует."}
            payload = {"name": name, "private": manifest["spec"]["private"], "description": manifest["spec"].get("description", ""), "auto_init": False}
            resp = requests.post("https://api.github.com/user/repos", headers=headers, json=payload)
            if resp.status_code == 201:
                data = resp.json()
                save_desired_state(f"github:repo:{name}", "github", "Repository", manifest["spec"])
                return {"status": "SUCCEEDED", "url": data["html_url"]}
            return {"status": "FAILED", "error": f"GitHub API: {resp.status_code}"}
        if op == "DELETE":
            user_resp = requests.get("https://api.github.com/user", headers=headers)
            if not user_resp.ok:
                return {"status": "FAILED"}
            owner = user_resp.json()["login"]
            resp = requests.delete(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
            if resp.status_code == 204:
                delete_desired_state(f"github:repo:{name}")
                return {"status": "SUCCEEDED", "message": "Репозиторий удалён"}
            return {"status": "NOOP" if resp.status_code == 404 else "FAILED"}
    if kind == "aws:S3:Bucket":
        if not AWS_ACCESS_KEY or not AWS_SECRET_KEY:
            return {"status": "FAILED", "error": "AWS ключи не заданы."}
        import boto3
        from botocore.exceptions import ClientError
        region = manifest["spec"].get("region", AWS_REGION)
        s3 = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY, region_name=region)
        if op == "CREATE":
            try:
                s3.head_bucket(Bucket=name)
                return {"status": "NOOP", "message": "Бакет существует."}
            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    if region == "us-east-1":
                        s3.create_bucket(Bucket=name)
                    else:
                        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": region})
                    save_desired_state(f"aws:s3:{name}", "aws", "Bucket", manifest["spec"])
                    return {"status": "SUCCEEDED", "url": f"https://s3.console.aws.amazon.com/s3/buckets/{name}"}
                return {"status": "FAILED", "error": str(e)}
        if op == "DELETE":
            try:
                s3.head_bucket(Bucket=name)
                s3.delete_bucket(Bucket=name)
                delete_desired_state(f"aws:s3:{name}")
                return {"status": "SUCCEEDED", "message": "Бакет удалён"}
            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    return {"status": "NOOP", "message": "Бакет не существует."}
                return {"status": "FAILED"}
    return {"status": "FAILED", "error": "Неизвестный провайдер"}

# ===================== ВСТРОЕННЫЙ WORKER =====================
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

# ===================== ВСТРОЕННЫЙ WEB UI =====================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>simpletools</title>
</head>
<body style="background:#0a0e14;color:#b0b8c4;font-family:sans-serif;padding:40px">
    <h1>simpletools</h1>
    <form method="post" action="/wish">
        <textarea name="wish_text" rows="3" style="width:100%;background:#131820;color:#e6edf3">{{ wish_text or '' }}</textarea>
        <br>
        <button type="submit" style="background:#7aa2f7;color:#fff;padding:12px 28px">SEE</button>
    </form>
    {% if plan %}
    <div style="background:#131820;padding:20px;margin-top:20px">
        <p>{{ plan.summary }}</p>
        {% if allowed %}
        <a href="/say/{{ operation_id }}/yes" style="background:#73daca;color:#000;padding:12px 28px;margin:5px">ДА</a>
        <a href="/say/{{ operation_id }}/no" style="border:1px solid #f7768e;color:#f7768e;padding:12px 28px;margin:5px">НЕТ</a>
        {% endif %}
    </div>
    {% endif %}
    <div style="margin-top:20px">
        {% for op in operations %}
        <p>{{ op.id }} {{ op.status }}</p>
        {% endfor %}
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, operations=list_operations(10))

@app.route("/wish", methods=["POST"])
def wish():
    wish_text = request.form.get("wish_text", "").strip()
    if not wish_text:
        return redirect(url_for("index"))
    parsed = parse_wish(wish_text)
    if "error" in parsed:
        return render_template_string(HTML_TEMPLATE, operations=list_operations(10), wish_text=wish_text, plan={"summary": f"Ошибка: {parsed['error']}"})
    manifest = formalize(parsed)
    plan = calculate_plan(manifest)
    policy_result = check_policies(manifest)
    op_id = f"op-{uuid.uuid4().hex[:8]}"
    save_operation(op_id, "PENDING", manifest)
    return render_template_string(HTML_TEMPLATE, operations=list_operations(10), wish_text=wish_text, plan=plan, operation_id=op_id, allowed=policy_result["allowed"])

@app.route("/say/<op_id>/<decision>")
def say(op_id, decision):
    op = get_operation(op_id)
    if not op:
        return "Not found", 404
    if decision == "no":
        save_operation(op_id, "REJECTED", op["manifest"])
    elif decision == "yes":
        save_operation(op_id, "IN_PROGRESS", op["manifest"])
        result = execute(op["manifest"])
        save_operation(op_id, result.get("status", "UNKNOWN"), op["manifest"], result)
    return redirect(url_for("index"))

# ===================== ИНТЕРАКТИВНЫЙ CLI =====================
def run_interactive(user_text):
    print("WISH:", user_text)
    parsed = parse_wish(user_text)
    if "error" in parsed:
        print(parsed["error"])
        return
    manifest = formalize(parsed)
    policy_result = check_policies(manifest)
    if policy_result["warnings"]:
        for w in policy_result["warnings"]:
            print(f"⚠️ {w}")
    if not policy_result["allowed"]:
        for e in policy_result["errors"]:
            print(f"❌ {e}")
        return
    plan = calculate_plan(manifest)
    print("SEE:", plan["summary"])
    answer = input("SAY [yes/no/modify]: ").strip().lower()
    if answer == "yes":
        op_id = f"op-{uuid.uuid4().hex[:8]}"
        save_operation(op_id, "IN_PROGRESS", manifest)
        result = execute(manifest)
        save_operation(op_id, result.get("status", "UNKNOWN"), manifest, result)
        print(result.get("status"))
    elif answer == "modify":
        run_interactive(input("Уточните: "))
    else:
        print("Отменено.")

# ===================== ТОЧКА ВХОДА =====================
if __name__ == "__main__":
    init_db()
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print("Команды: wish, operation list, operation show, worker, web")
    elif sys.argv[1] == "wish":
        run_interactive(" ".join(sys.argv[2:]).strip('"').strip("'"))
    elif sys.argv[1] == "operation":
        if sys.argv[2] == "list":
            for o in list_operations():
                print(o["id"], o["status"])
        elif sys.argv[2] == "show":
            op = get_operation(sys.argv[3])
            if op:
                print(json.dumps(op, indent=2, ensure_ascii=False))
    elif sys.argv[1] == "worker":
        run_worker()
    elif sys.argv[1] == "web":
        print("🌐 Запуск веб-интерфейса: http://localhost:5000")
        app.run(host="0.0.0.0", port=5000, debug=True)
    else:
        print(f"Неизвестная команда: {sys.argv[1]}")
