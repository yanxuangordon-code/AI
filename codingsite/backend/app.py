from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)
from werkzeug.security import generate_password_hash, check_password_hash
import subprocess, sqlite3, uuid, os, tempfile, random, string
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ── Config ──────────────────────────────────────────────────────────────────
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "change-me-in-production")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=30)

app.config["MAIL_SERVER"]   = "smtp.gmail.com"
app.config["MAIL_PORT"]     = 587
app.config["MAIL_USE_TLS"]  = True
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")   # your Gmail
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")   # Gmail app password
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_USERNAME")

jwt  = JWTManager(app)
mail = Mail(app)

# ── Database ─────────────────────────────────────────────────────────────────
def get_db():
    con = sqlite3.connect("codesponge.db")
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id        TEXT PRIMARY KEY,
            email     TEXT UNIQUE NOT NULL,
            password  TEXT NOT NULL,
            verified  INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS verification_codes (
            email      TEXT PRIMARY KEY,
            code       TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            name       TEXT NOT NULL,
            language   TEXT DEFAULT 'python',
            code       TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS snippets (
            id         TEXT PRIMARY KEY,
            language   TEXT,
            code       TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    con.commit()
    con.close()

init_db()

# ── Helpers ──────────────────────────────────────────────────────────────────
def make_code():
    return "".join(random.choices(string.digits, k=6))

# ── Auth: Send verification code ─────────────────────────────────────────────
@app.route("/auth/send-code", methods=["POST"])
def send_code():
    data  = request.json
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400

    code    = make_code()
    expires = datetime.utcnow() + timedelta(minutes=10)

    con = get_db()
    con.execute(
        "INSERT OR REPLACE INTO verification_codes (email, code, expires_at) VALUES (?,?,?)",
        (email, code, expires)
    )
    con.commit()
    con.close()

    try:
        msg = Message(
            subject="Your CodeSponge verification code",
            recipients=[email],
            body=f"Your verification code is: {code}\n\nIt expires in 10 minutes."
        )
        mail.send(msg)
    except Exception as e:
        return jsonify({"error": f"Could not send email: {str(e)}"}), 500

    return jsonify({"message": "Code sent"})

# ── Auth: Sign up ─────────────────────────────────────────────────────────────
@app.route("/auth/signup", methods=["POST"])
def signup():
    data     = request.json
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    code     = data.get("code", "").strip()

    if not email or not password or not code:
        return jsonify({"error": "All fields are required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    con = get_db()

    # Check code
    row = con.execute(
        "SELECT code, expires_at FROM verification_codes WHERE email=?", (email,)
    ).fetchone()
    if not row:
        con.close()
        return jsonify({"error": "No verification code found. Request a new one."}), 400
    if row["code"] != code:
        con.close()
        return jsonify({"error": "Incorrect verification code"}), 400
    if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
        con.close()
        return jsonify({"error": "Verification code has expired"}), 400

    # Check if email already exists
    existing = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        con.close()
        return jsonify({"error": "An account with this email already exists"}), 400

    user_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO users (id, email, password, verified) VALUES (?,?,?,1)",
        (user_id, email, generate_password_hash(password))
    )
    con.execute("DELETE FROM verification_codes WHERE email=?", (email,))
    con.commit()
    con.close()

    token = create_access_token(identity=user_id)
    return jsonify({"token": token, "email": email})

# ── Auth: Login ───────────────────────────────────────────────────────────────
@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.json
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password", "")

    con = get_db()
    user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    con.close()

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401

    token = create_access_token(identity=user["id"])
    return jsonify({"token": token, "email": email})

# ── Projects ──────────────────────────────────────────────────────────────────
@app.route("/projects", methods=["GET"])
@jwt_required()
def list_projects():
    user_id = get_jwt_identity()
    con = get_db()
    rows = con.execute(
        "SELECT id, name, language, updated_at FROM projects WHERE user_id=? ORDER BY updated_at DESC",
        (user_id,)
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/projects", methods=["POST"])
@jwt_required()
def create_project():
    user_id = get_jwt_identity()
    data    = request.json
    name    = (data.get("name") or "Untitled").strip()
    lang    = data.get("language", "python")
    proj_id = str(uuid.uuid4())[:8]

    con = get_db()
    con.execute(
        "INSERT INTO projects (id, user_id, name, language, code) VALUES (?,?,?,?,?)",
        (proj_id, user_id, name, lang, get_default_code(lang))
    )
    con.commit()
    con.close()
    return jsonify({"id": proj_id, "name": name, "language": lang})

@app.route("/projects/<proj_id>", methods=["GET"])
@jwt_required()
def get_project(proj_id):
    user_id = get_jwt_identity()
    con = get_db()
    row = con.execute(
        "SELECT * FROM projects WHERE id=? AND user_id=?", (proj_id, user_id)
    ).fetchone()
    con.close()
    if not row:
        return jsonify({"error": "Project not found"}), 404
    return jsonify(dict(row))

@app.route("/projects/<proj_id>", methods=["PUT"])
@jwt_required()
def update_project(proj_id):
    user_id = get_jwt_identity()
    data    = request.json
    con = get_db()
    con.execute(
        "UPDATE projects SET code=?, language=?, updated_at=? WHERE id=? AND user_id=?",
        (data.get("code",""), data.get("language","python"),
         datetime.utcnow(), proj_id, user_id)
    )
    con.commit()
    con.close()
    return jsonify({"message": "Saved"})

@app.route("/projects/<proj_id>", methods=["DELETE"])
@jwt_required()
def delete_project(proj_id):
    user_id = get_jwt_identity()
    con = get_db()
    con.execute("DELETE FROM projects WHERE id=? AND user_id=?", (proj_id, user_id))
    con.commit()
    con.close()
    return jsonify({"message": "Deleted"})

# ── Run Code ──────────────────────────────────────────────────────────────────
@app.route("/run", methods=["POST"])
@jwt_required()
def run_code():
    data     = request.json
    language = data.get("language")
    code     = data.get("code", "")
    try:
        if language == "python":
            result = run_python(code)
        elif language == "javascript":
            result = run_javascript(code)
        elif language == "cpp":
            result = run_cpp(code)
        elif language == "html":
            return jsonify({"output": "", "error": ""})
        else:
            return jsonify({"error": "Unsupported language"}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"output": "", "error": str(e)})

def run_python(code):
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write(code); fname = f.name
    try:
        p = subprocess.run(["python3", fname], capture_output=True, text=True, timeout=10)
        return {"output": p.stdout, "error": p.stderr}
    finally:
        os.unlink(fname)

def run_javascript(code):
    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
        f.write(code); fname = f.name
    try:
        p = subprocess.run(["node", fname], capture_output=True, text=True, timeout=10)
        return {"output": p.stdout, "error": p.stderr}
    finally:
        os.unlink(fname)

def run_cpp(code):
    with tempfile.NamedTemporaryFile(suffix=".cpp", delete=False, mode="w") as f:
        f.write(code); src = f.name
    out = src.replace(".cpp", "")
    try:
        cp = subprocess.run(["g++", src, "-o", out], capture_output=True, text=True, timeout=15)
        if cp.returncode != 0:
            return {"output": "", "error": cp.stderr}
        rp = subprocess.run([out], capture_output=True, text=True, timeout=10)
        return {"output": rp.stdout, "error": rp.stderr}
    finally:
        os.unlink(src)
        if os.path.exists(out): os.unlink(out)

# ── Share Snippets (public, no auth) ─────────────────────────────────────────
@app.route("/snippets", methods=["POST"])
def save_snippet():
    data = request.json
    sid  = str(uuid.uuid4())[:8]
    con  = get_db()
    con.execute("INSERT INTO snippets (id, language, code) VALUES (?,?,?)",
                (sid, data.get("language"), data.get("code")))
    con.commit(); con.close()
    return jsonify({"id": sid})

@app.route("/snippets/<sid>", methods=["GET"])
def get_snippet(sid):
    con = get_db()
    row = con.execute("SELECT language, code FROM snippets WHERE id=?", (sid,)).fetchone()
    con.close()
    if row: return jsonify(dict(row))
    return jsonify({"error": "Not found"}), 404

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_default_code(lang):
    defaults = {
        "python":     '# Python\nprint("Hello, World!")',
        "javascript": '// JavaScript\nconsole.log("Hello, World!");',
        "cpp":        '#include <iostream>\nusing namespace std;\n\nint main() {\n    cout << "Hello, World!" << endl;\n    return 0;\n}',
        "html":       '<!DOCTYPE html>\n<html>\n<body>\n  <h1>Hello, World!</h1>\n</body>\n</html>',
    }
    return defaults.get(lang, "")

if __name__ == "__main__":
    app.run(debug=True)
