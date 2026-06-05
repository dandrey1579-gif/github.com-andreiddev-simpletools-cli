import yaml, os

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
