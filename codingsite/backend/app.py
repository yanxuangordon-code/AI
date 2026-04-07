from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import sqlite3
import uuid
import os
import tempfile

app = Flask(__name__)
CORS(app)

# ---------- Database ----------
def init_db():
    con = sqlite3.connect("snippets.db")
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snippets (
            id TEXT PRIMARY KEY,
            language TEXT,
            code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

init_db()

# ---------- Run Code ----------
@app.route("/run", methods=["POST"])
def run_code():
    data = request.json
    language = data.get("language")
    code = data.get("code", "")

    try:
        if language == "python":
            result = run_python(code)
        elif language == "javascript":
            result = run_javascript(code)
        elif language == "cpp":
            result = run_cpp(code)
        elif language == "html":
            # HTML runs in browser, no server execution needed
            return jsonify({"output": "", "error": ""})
        else:
            return jsonify({"error": "Unsupported language"}), 400

        return jsonify(result)
    except Exception as e:
        return jsonify({"output": "", "error": str(e)})


def run_python(code):
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write(code)
        fname = f.name
    try:
        proc = subprocess.run(
            ["python3", fname],
            capture_output=True, text=True, timeout=10
        )
        return {"output": proc.stdout, "error": proc.stderr}
    finally:
        os.unlink(fname)


def run_javascript(code):
    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
        f.write(code)
        fname = f.name
    try:
        proc = subprocess.run(
            ["node", fname],
            capture_output=True, text=True, timeout=10
        )
        return {"output": proc.stdout, "error": proc.stderr}
    finally:
        os.unlink(fname)


def run_cpp(code):
    with tempfile.NamedTemporaryFile(suffix=".cpp", delete=False, mode="w") as f:
        f.write(code)
        src = f.name
    out = src.replace(".cpp", "")
    try:
        compile_proc = subprocess.run(
            ["g++", src, "-o", out],
            capture_output=True, text=True, timeout=15
        )
        if compile_proc.returncode != 0:
            return {"output": "", "error": compile_proc.stderr}
        run_proc = subprocess.run(
            [out],
            capture_output=True, text=True, timeout=10
        )
        return {"output": run_proc.stdout, "error": run_proc.stderr}
    finally:
        os.unlink(src)
        if os.path.exists(out):
            os.unlink(out)


# ---------- Save Snippet ----------
@app.route("/snippets", methods=["POST"])
def save_snippet():
    data = request.json
    snippet_id = str(uuid.uuid4())[:8]
    con = sqlite3.connect("snippets.db")
    cur = con.cursor()
    cur.execute(
        "INSERT INTO snippets (id, language, code) VALUES (?, ?, ?)",
        (snippet_id, data.get("language"), data.get("code"))
    )
    con.commit()
    con.close()
    return jsonify({"id": snippet_id})


# ---------- Load Snippet ----------
@app.route("/snippets/<snippet_id>", methods=["GET"])
def get_snippet(snippet_id):
    con = sqlite3.connect("snippets.db")
    cur = con.cursor()
    cur.execute("SELECT language, code FROM snippets WHERE id = ?", (snippet_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return jsonify({"language": row[0], "code": row[1]})
    return jsonify({"error": "Snippet not found"}), 404


if __name__ == "__main__":
    app.run(debug=True)
