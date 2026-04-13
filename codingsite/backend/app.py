from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity, verify_jwt_in_request
)
from werkzeug.security import generate_password_hash, check_password_hash
import resend
import subprocess, uuid, os, tempfile, random, string, json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ── Config ────────────────────────────────────────────────────────────────────
app.config["JWT_SECRET_KEY"]           = os.environ.get("JWT_SECRET_KEY", "change-me")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=30)
resend.api_key = os.environ.get("RESEND_API_KEY")
jwt = JWTManager(app)

DATABASE_URL = os.environ.get("DATABASE_URL")
SITES_DIR    = os.path.join(os.path.dirname(__file__), "sites")
os.makedirs(SITES_DIR, exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    con = psycopg2.connect(DATABASE_URL)
    con.autocommit = False
    return con
@app.route("/auth/oauth", methods=["POST"])
def oauth_login():
    data     = request.json
    email    = (data.get("email") or "").strip().lower()
    name     = data.get("name") or ""
    if not email:
        return jsonify({"error": "No email provided"}), 400
    con = get_db(); cur = con.cursor()
    cur.execute("SELECT id, username FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    if row:
        # Existing user — log in
        user_id  = row[0]
        username = row[1] or email.split("@")[0]
    else:
        # New user — create account
        user_id  = str(uuid.uuid4())
        username = email.split("@")[0]
        cur.execute("INSERT INTO users (id, email, password, username, verified) VALUES (%s,%s,%s,%s,1)",
                    (user_id, email, generate_password_hash(str(uuid.uuid4())), username))
    con.commit(); con.close()
    token = create_access_token(identity=user_id)
    return jsonify({"token": token, "email": email, "username": username})

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         TEXT PRIMARY KEY,
            email      TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            username   TEXT,
            bio        TEXT DEFAULT '',
            verified   INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verification_codes (
            email      TEXT PRIMARY KEY,
            code       TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            name         TEXT NOT NULL,
            language     TEXT DEFAULT 'python',
            code         TEXT DEFAULT '',
            project_type TEXT DEFAULT 'single',
            files        TEXT DEFAULT '[]',
            hosted_url   TEXT DEFAULT NULL,
            updated_at   TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS snippets (
            id         TEXT PRIMARY KEY,
            language   TEXT,
            code       TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS community_posts (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            project_id   TEXT,
            title        TEXT NOT NULL,
            description  TEXT DEFAULT '',
            language     TEXT DEFAULT '',
            project_type TEXT DEFAULT 'single',
            files        TEXT DEFAULT '[]',
            code         TEXT DEFAULT '',
            hosted_url   TEXT DEFAULT NULL,
            likes        INTEGER DEFAULT 0,
            forks        INTEGER DEFAULT 0,
            views        INTEGER DEFAULT 0,
            published_at TIMESTAMP DEFAULT NOW(),
            updated_at   TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS post_likes (
            user_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            PRIMARY KEY(user_id, post_id)
        )
    """)
    con.commit()
    con.close()

init_db()
@app.route("/ping")
def ping():
    return jsonify({"ok": True})

# ── Helpers ───────────────────────────────────────────────────────────────────
def make_code():
    return "".join(random.choices(string.digits, k=6))

def get_default_code(lang):
    defaults = {
        "python":     '# Python\nprint("Hello, World!")',
        "javascript": '// JavaScript\nconsole.log("Hello, World!");',
        "cpp":        '#include <iostream>\nusing namespace std;\n\nint main() {\n    cout << "Hello, World!" << endl;\n    return 0;\n}',
        "html":       '<!DOCTYPE html>\n<html>\n<body>\n  <h1>Hello, World!</h1>\n</body>\n</html>',
    }
    return defaults.get(lang, "")

def get_default_html():
    return '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8"/>\n  <title>My Website</title>\n  <link rel="stylesheet" href="style.css"/>\n</head>\n<body>\n  <h1>Hello, World!</h1>\n  <script src="script.js"></script>\n</body>\n</html>'

def get_default_css():
    return '*, *::before, *::after { box-sizing: border-box; }\nbody {\n  font-family: sans-serif;\n  margin: 0; padding: 40px;\n  background: #0d1117;\n  color: #e6edf3;\n}\nh1 { font-size: 2rem; }'

def optional_jwt_identity():
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except:
        return None

def row_to_dict(cur, row):
    if row is None:
        return None
    return {cur.description[i][0]: row[i] for i in range(len(row))}

def rows_to_dicts(cur, rows):
    return [row_to_dict(cur, r) for r in rows]

# ── Code runners (defined BEFORE /run route) ──────────────────────────────────
def run_python(code):
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as f:
        f.write(code)
        fname = f.name
    try:
        p = subprocess.run(
            ["python3", "-u", fname],
            capture_output=True, text=True, timeout=10
        )
        return {"output": p.stdout, "error": p.stderr}
    except subprocess.TimeoutExpired:
        return {"output": "", "error": "Error: execution timed out after 10 seconds."}
    finally:
        os.unlink(fname)

def run_javascript(code):
    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w", encoding="utf-8") as f:
        f.write(code)
        fname = f.name
    try:
        p = subprocess.run(
            ["node", "--stack-trace-limit=5", fname],
            capture_output=True, text=True, timeout=10
        )
        return {"output": p.stdout, "error": p.stderr}
    except subprocess.TimeoutExpired:
        return {"output": "", "error": "Error: execution timed out after 10 seconds."}
    finally:
        os.unlink(fname)

def run_cpp(code):
    with tempfile.NamedTemporaryFile(suffix=".cpp", delete=False, mode="w", encoding="utf-8") as f:
        f.write(code)
        src = f.name
    out_bin = src.replace(".cpp", "")
    try:
        cp = subprocess.run(
            ["g++", "-o", out_bin, src, "-std=c++17", "-Wall"],
            capture_output=True, text=True, timeout=15
        )
        if cp.returncode != 0:
            return {"output": "", "error": cp.stderr}
        rp = subprocess.run(
            [out_bin],
            capture_output=True, text=True, timeout=10
        )
        return {"output": rp.stdout, "error": rp.stderr}
    except subprocess.TimeoutExpired:
        return {"output": "", "error": "Error: execution timed out after 10 seconds."}
    finally:
        os.unlink(src)
        if os.path.exists(out_bin):
            os.unlink(out_bin)

# ── Auth: Send verification code ──────────────────────────────────────────────
@app.route("/auth/send-code", methods=["POST"])
def send_code():
    data  = request.json
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400

    code    = make_code()
    expires = datetime.utcnow() + timedelta(minutes=10)

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO verification_codes (email, code, expires_at) VALUES (%s,%s,%s) "
        "ON CONFLICT (email) DO UPDATE SET code=%s, expires_at=%s",
        (email, code, expires, code, expires)
    )
    con.commit()
    con.close()

    try:
        resend.Emails.send({
            "from":    "CodeSponge <onboarding@resend.dev>",
            "to":      [email],
            "subject": "Your CodeSponge verification code",
            "text":    f"Your verification code is: {code}\n\nIt expires in 10 minutes."
        })
    except Exception as e:
        return jsonify({"error": f"Could not send email: {str(e)}"}), 500

    return jsonify({"message": "Code sent"})

# ── Auth: Signup ──────────────────────────────────────────────────────────────
@app.route("/auth/signup", methods=["POST"])
def signup():
    data     = request.json
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    code     = data.get("code", "").strip()
    username = (data.get("username") or "").strip().lower()

    if not email or not password or not code:
        return jsonify({"error": "All fields are required"}), 400
    if not username or len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT code, expires_at FROM verification_codes WHERE email=%s", (email,))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({"error": "No verification code found. Request a new one."}), 400
    if row[0] != code:
        con.close()
        return jsonify({"error": "Incorrect verification code."}), 400
    if row[1] < datetime.utcnow():
        con.close()
        return jsonify({"error": "Verification code has expired."}), 400

    cur.execute("SELECT id FROM users WHERE email=%s", (email,))
    if cur.fetchone():
        con.close()
        return jsonify({"error": "An account with this email already exists."}), 400

    cur.execute("SELECT id FROM users WHERE username=%s", (username,))
    if cur.fetchone():
        con.close()
        return jsonify({"error": "That username is already taken."}), 400

    user_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO users (id, email, password, username, verified) VALUES (%s,%s,%s,%s,1)",
        (user_id, email, generate_password_hash(password), username)
    )
    cur.execute("DELETE FROM verification_codes WHERE email=%s", (email,))
    con.commit()
    con.close()

    token = create_access_token(identity=user_id)
    return jsonify({"token": token, "email": email, "username": username})

# ── Auth: Login ───────────────────────────────────────────────────────────────
@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.json
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password", "")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id, password, username FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    con.close()

    if not row or not check_password_hash(row[1], password):
        return jsonify({"error": "Invalid email or password."}), 401

    token = create_access_token(identity=row[0])
    return jsonify({"token": token, "email": email, "username": row[2] or ""})

# ── Projects: List ────────────────────────────────────────────────────────────
@app.route("/projects", methods=["GET"])
@jwt_required()
def list_projects():
    user_id = get_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT id, name, language, project_type, hosted_url, updated_at "
        "FROM projects WHERE user_id=%s ORDER BY updated_at DESC",
        (user_id,)
    )
    rows = rows_to_dicts(cur, cur.fetchall())
    con.close()
    return jsonify(rows)

# ── Projects: Create ──────────────────────────────────────────────────────────
@app.route("/projects", methods=["POST"])
@jwt_required()
def create_project():
    user_id = get_jwt_identity()
    data    = request.json
    name    = (data.get("name") or "").strip()
    ptype   = data.get("project_type", "single")
    lang    = data.get("language", "python") if ptype == "single" else "html"
    proj_id = str(uuid.uuid4())[:8]

    if not name:
        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM projects WHERE user_id=%s", (user_id,))
        count = cur.fetchone()[0]
        con.close()
        name = "Project {:03d}".format(count + 1)

    if ptype == "repo":
        files = json.dumps([
            {"id": "f1", "name": "index.html",  "content": get_default_html(), "language": "html"},
            {"id": "f2", "name": "style.css",   "content": get_default_css(),  "language": "css"},
            {"id": "f3", "name": "script.js",   "content": "// script.js\nconsole.log('Hello!');", "language": "javascript"},
        ])
        code = ""
    else:
        files = "[]"
        code  = get_default_code(lang)

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO projects (id, user_id, name, language, code, project_type, files) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (proj_id, user_id, name, lang, code, ptype, files)
    )
    con.commit()
    con.close()
    return jsonify({"id": proj_id, "name": name, "language": lang, "project_type": ptype})

# ── Projects: Get ─────────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>", methods=["GET"])
@jwt_required()
def get_project(proj_id):
    user_id = get_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM projects WHERE id=%s AND user_id=%s", (proj_id, user_id))
    row = row_to_dict(cur, cur.fetchone())
    con.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    try:
        row["files"] = json.loads(row.get("files") or "[]")
    except:
        row["files"] = []
    return jsonify(row)

# ── Projects: Update ──────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>", methods=["PUT"])
@jwt_required()
def update_project(proj_id):
    user_id = get_jwt_identity()
    data    = request.json
    con = get_db()
    cur = con.cursor()

    fields, values = [], []
    if "name"       in data: fields.append("name=%s");       values.append(data["name"])
    if "code"       in data: fields.append("code=%s");       values.append(data.get("code", ""))
    if "language"   in data: fields.append("language=%s");   values.append(data.get("language", "python"))
    if "files"      in data: fields.append("files=%s");      values.append(json.dumps(data["files"]))
    if "hosted_url" in data: fields.append("hosted_url=%s"); values.append(data.get("hosted_url"))
    fields.append("updated_at=%s")
    values.append(datetime.utcnow())
    values.extend([proj_id, user_id])

    cur.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id=%s AND user_id=%s", values)
    con.commit()
    con.close()
    return jsonify({"message": "Saved"})

# ── Projects: Delete ──────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>", methods=["DELETE"])
@jwt_required()
def delete_project(proj_id):
    user_id = get_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM projects WHERE id=%s AND user_id=%s", (proj_id, user_id))
    con.commit()
    con.close()
    return jsonify({"message": "Deleted"})

# ── Host website ──────────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>/host", methods=["POST"])
@jwt_required()
def host_project(proj_id):
    user_id = get_jwt_identity()
    data    = request.json
    files   = data.get("files", [])

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT id FROM projects WHERE id=%s AND user_id=%s", (proj_id, user_id))
    if not cur.fetchone():
        con.close()
        return jsonify({"error": "Not found"}), 404

    site_dir = os.path.join(SITES_DIR, proj_id)
    os.makedirs(site_dir, exist_ok=True)
    for f in files:
        safe = os.path.basename(f.get("name", "index.html"))
        with open(os.path.join(site_dir, safe), "w", encoding="utf-8") as fp:
            fp.write(f.get("content", ""))

    base = os.environ.get("RENDER_EXTERNAL_URL", "https://codesponge-backend.onrender.com")
    url  = f"{base}/sites/{proj_id}/index.html"

    cur.execute("UPDATE projects SET hosted_url=%s, updated_at=%s WHERE id=%s", (url, datetime.utcnow(), proj_id))
    con.commit()
    con.close()
    return jsonify({"url": url})

@app.route("/sites/<proj_id>/<path:filename>")
def serve_site(proj_id, filename):
    return send_from_directory(os.path.join(SITES_DIR, proj_id), os.path.basename(filename))

# ── Run code ──────────────────────────────────────────────────────────────────
@app.route("/run", methods=["POST"])
@jwt_required()
def run_code():
    data = request.json
    lang = data.get("language")
    code = data.get("code", "")
    try:
        if   lang == "python":     result = run_python(code)
        elif lang == "javascript": result = run_javascript(code)
        elif lang == "cpp":        result = run_cpp(code)
        elif lang == "html":       return jsonify({"output": "", "error": ""})
        else:                      return jsonify({"error": "Unsupported language"}), 400

        # Show partial output before error if both exist
        if result.get("output") and result.get("error"):
            result["output"] = result["output"].rstrip() + "\n\n" + result["error"]

        return jsonify(result)
    except Exception as e:
        return jsonify({"output": "", "error": str(e)})

# ── Snippets ──────────────────────────────────────────────────────────────────
@app.route("/snippets", methods=["POST"])
def save_snippet():
    data = request.json
    sid  = str(uuid.uuid4())[:8]
    con  = get_db()
    cur  = con.cursor()
    cur.execute(
        "INSERT INTO snippets (id, language, code) VALUES (%s,%s,%s)",
        (sid, data.get("language"), data.get("code"))
    )
    con.commit()
    con.close()
    return jsonify({"id": sid})

@app.route("/snippets/<sid>", methods=["GET"])
def get_snippet(sid):
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT language, code FROM snippets WHERE id=%s", (sid,))
    row = row_to_dict(cur, cur.fetchone())
    con.close()
    if row:
        return jsonify(row)
    return jsonify({"error": "Not found"}), 404

# ── Community: List ───────────────────────────────────────────────────────────
@app.route("/community", methods=["GET"])
def list_community():
    sort      = request.args.get("sort", "recent")
    lang      = request.args.get("language", "")
    search    = request.args.get("q", "").strip()
    limit     = min(int(request.args.get("limit", 20)), 50)
    offset    = int(request.args.get("offset", 0))
    viewer_id = optional_jwt_identity()

    order = {
        "trending": "cp.views DESC, cp.likes DESC",
        "top":      "cp.likes DESC",
        "recent":   "cp.published_at DESC",
    }.get(sort, "cp.published_at DESC")

    where, qparams = [], []
    if lang:
        where.append("cp.language=%s")
        qparams.append(lang)
    if search:
        where.append("(cp.title ILIKE %s OR cp.description ILIKE %s OR u.username ILIKE %s)")
        qparams.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    con = get_db()
    cur = con.cursor()
    cur.execute(f"""
        SELECT cp.id, cp.title, cp.description, cp.language, cp.project_type,
               cp.code, cp.files, cp.hosted_url,
               cp.likes, cp.forks, cp.views, cp.published_at,
               u.username, u.email,
               CASE WHEN pl.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked_by_me
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        LEFT JOIN post_likes pl ON pl.post_id = cp.id AND pl.user_id = %s
        {where_sql}
        ORDER BY {order}
        LIMIT %s OFFSET %s
    """, [viewer_id or "", *qparams, limit, offset])
    rows = rows_to_dicts(cur, cur.fetchall())

    cur.execute(
        f"SELECT COUNT(*) FROM community_posts cp JOIN users u ON cp.user_id=u.id {where_sql}",
        qparams
    )
    total = cur.fetchone()[0]
    con.close()

    for r in rows:
        code = r.get("code") or ""
        try:
            files = json.loads(r.get("files") or "[]")
            if files and not code:
                code = files[0].get("content", "")
        except:
            files = []
        r["code_preview"] = code[:400]
        r["files"] = files
        del r["code"]

    return jsonify({"posts": rows, "total": total})

# ── Community: Publish ────────────────────────────────────────────────────────
@app.route("/community/publish", methods=["POST"])
@jwt_required()
def publish_project():
    user_id = get_jwt_identity()
    data    = request.json
    proj_id = data.get("project_id")
    title   = (data.get("title") or "").strip()
    desc    = (data.get("description") or "").strip()
    if not title:
        return jsonify({"error": "Title required"}), 400

    code = ""; files = []; lang = ""; ptype = "single"; hosted_url = None

    if proj_id:
        con = get_db()
        cur = con.cursor()
        cur.execute("SELECT * FROM projects WHERE id=%s AND user_id=%s", (proj_id, user_id))
        p = row_to_dict(cur, cur.fetchone())
        con.close()
        if p:
            code       = p.get("code", "")
            lang       = p.get("language", "")
            ptype      = p.get("project_type", "single")
            hosted_url = p.get("hosted_url")
            try:
                files = json.loads(p.get("files") or "[]")
            except:
                files = []

    post_id = str(uuid.uuid4())[:8]
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO community_posts
        (id, user_id, project_id, title, description, language, project_type, code, files, hosted_url)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (post_id, user_id, proj_id, title, desc, lang, ptype, code, json.dumps(files), hosted_url))
    con.commit()
    con.close()
    return jsonify({"id": post_id, "message": "Published!"})

# ── Community: Get post ───────────────────────────────────────────────────────
@app.route("/community/<post_id>", methods=["GET"])
def get_post(post_id):
    viewer_id = optional_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE community_posts SET views=views+1 WHERE id=%s", (post_id,))
    cur.execute("""
        SELECT cp.*, u.username, u.email, u.bio,
               CASE WHEN pl.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked_by_me
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        LEFT JOIN post_likes pl ON pl.post_id = cp.id AND pl.user_id = %s
        WHERE cp.id = %s
    """, (viewer_id or "", post_id))
    row = row_to_dict(cur, cur.fetchone())
    con.commit()
    con.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    try:
        row["files"] = json.loads(row.get("files") or "[]")
    except:
        row["files"] = []
    return jsonify(row)

# ── Community: Like / Unlike ──────────────────────────────────────────────────
@app.route("/community/<post_id>/like", methods=["POST"])
@jwt_required()
def like_post(post_id):
    user_id = get_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT 1 FROM post_likes WHERE user_id=%s AND post_id=%s", (user_id, post_id))
    if cur.fetchone():
        cur.execute("DELETE FROM post_likes WHERE user_id=%s AND post_id=%s", (user_id, post_id))
        cur.execute("UPDATE community_posts SET likes=GREATEST(0,likes-1) WHERE id=%s", (post_id,))
        liked = False
    else:
        cur.execute("INSERT INTO post_likes (user_id,post_id) VALUES (%s,%s)", (user_id, post_id))
        cur.execute("UPDATE community_posts SET likes=likes+1 WHERE id=%s", (post_id,))
        liked = True
    cur.execute("SELECT likes FROM community_posts WHERE id=%s", (post_id,))
    likes = cur.fetchone()[0]
    con.commit()
    con.close()
    return jsonify({"liked": liked, "likes": likes})

# ── Community: Fork ───────────────────────────────────────────────────────────
@app.route("/community/<post_id>/fork", methods=["POST"])
@jwt_required()
def fork_post(post_id):
    user_id = get_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM community_posts WHERE id=%s", (post_id,))
    p = row_to_dict(cur, cur.fetchone())
    if not p:
        con.close()
        return jsonify({"error": "Not found"}), 404

    new_id = str(uuid.uuid4())[:8]
    cur.execute(
        "INSERT INTO projects (id, user_id, name, language, code, project_type, files) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (new_id, user_id, "Fork of " + p["title"],
         p.get("language", "python"), p.get("code", ""),
         p.get("project_type", "single"), p.get("files", "[]"))
    )
    cur.execute("UPDATE community_posts SET forks=forks+1 WHERE id=%s", (post_id,))
    con.commit()
    con.close()
    return jsonify({"project_id": new_id, "project_type": p.get("project_type", "single"), "message": "Forked!"})

# ── Community: Delete ─────────────────────────────────────────────────────────
@app.route("/community/<post_id>", methods=["DELETE"])
@jwt_required()
def delete_post(post_id):
    user_id = get_jwt_identity()
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM community_posts WHERE id=%s AND user_id=%s", (post_id, user_id))
    cur.execute("DELETE FROM post_likes WHERE post_id=%s", (post_id,))
    con.commit()
    con.close()
    return jsonify({"message": "Deleted"})

# ── User profile ──────────────────────────────────────────────────────────────
@app.route("/users/<username>", methods=["GET"])
def get_profile(username):
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT id, username, email, bio, created_at FROM users WHERE username=%s",
        (username,)
    )
    user = row_to_dict(cur, cur.fetchone())
    if not user:
        con.close()
        return jsonify({"error": "Not found"}), 404
    cur.execute(
        "SELECT id, title, description, language, project_type, hosted_url, "
        "likes, forks, views, published_at FROM community_posts "
        "WHERE user_id=%s ORDER BY published_at DESC",
        (user["id"],)
    )
    posts = rows_to_dicts(cur, cur.fetchall())
    con.close()
    return jsonify({
        "username":   user["username"],
        "email":      user["email"],
        "bio":        user["bio"],
        "created_at": str(user["created_at"]),
        "posts":      posts
    })

if __name__ == "__main__":
    app.run(debug=True)
