from flask import Flask, render_template_string, request, redirect, url_for
import uuid, os, sys
from db import init_db, save_operation, get_operation, list_operations

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
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from simpletools import parse_wish, formalize, calculate_plan
    parsed = parse_wish(wish_text)
    if "error" in parsed:
        return render_template_string(HTML_TEMPLATE, operations=list_operations(10), wish_text=wish_text, plan={"summary": f"Ошибка: {parsed['error']}"})
    manifest = formalize(parsed)
    plan = calculate_plan(manifest)
    from policy_engine import check_policies
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
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from simpletools import execute
        result = execute(op["manifest"])
        save_operation(op_id, result.get("status", "UNKNOWN"), op["manifest"], result)
    return redirect(url_for("index"))

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
