#!/usr/bin/env python3
"""simpletools CLI v1.0 – Управление инфраструктурой через текстовые команды."""
import sys
import os

# Гарантирует, что модули проекта (db, policy_engine и др.) всегда будут найдены
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import uuid
import re
from datetime import datetime

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")

from db import init_db, save_operation, get_operation, list_operations, save_desired_state, delete_desired_state

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

def run_interactive(user_text):
    print("WISH:", user_text)
    parsed = parse_wish(user_text)
    if "error" in parsed:
        print(parsed["error"])
        return
    manifest = formalize(parsed)
    from policy_engine import check_policies
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
        from worker import run_worker
        run_worker()
    elif sys.argv[1] == "web":
        from webui import app
        app.run(host="0.0.0.0", port=5000)
