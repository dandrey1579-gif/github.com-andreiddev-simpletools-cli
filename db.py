import sqlite3, json, os
from datetime import datetime

DB_PATH = os.environ.get("SIMPLETOOLS_DB", "simpletools.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, status TEXT DEFAULT 'PENDING', manifest TEXT, result TEXT, created_at TEXT, updated_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS desired_state (resource_id TEXT PRIMARY KEY, provider TEXT, resource_type TEXT, spec TEXT, labels TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT)''')
    conn.commit()
    conn.close()

def save_operation(oid, status, manifest=None, result=None):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("INSERT OR REPLACE INTO operations (id, status, manifest, result, created_at, updated_at) VALUES (?,?,?,?,?,?)", (oid, status, json.dumps(manifest), json.dumps(result), now, now))
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
    c.execute("INSERT OR REPLACE INTO desired_state (resource_id, provider, resource_type, spec, labels, created_at, updated_at) VALUES (?,?,?,?,?,?,?)", (rid, provider, rtype, json.dumps(spec), json.dumps(labels or {}), now, now))
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
