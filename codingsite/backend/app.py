from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity, verify_jwt_in_request
)
from werkzeug.security import generate_password_hash, check_password_hash
import resend
import subprocess, sqlite3, uuid, os, tempfile, random, string, json
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ── Config ───────────────────────────────────────────────────────────────────
app.config["JWT_SECRET_KEY"]          = os.environ.get("JWT_SECRET_KEY", "change-me-in-production")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=30)

resend.api_key = os.environ.get("RESEND_API_KEY")
jwt = JWTManager(app)

# ── Hosted sites directory ────────────────────────────────────────────────────
SITES_DIR = os.path.join(os.path.dirname(__file__), "sites")
os.makedirs(SITES_DIR, exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    con = sqlite3.connect("codesponge.db")
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = get_db()
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         TEXT PRIMARY KEY,
            email      TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            username   TEXT,
            bio        TEXT DEFAULT '',
            verified   INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS verification_codes (
            email      TEXT PRIMARY KEY,
            code       TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            name         TEXT NOT NULL,
            language     TEXT DEFAULT 'python',
            code         TEXT DEFAULT '',
            project_type TEXT DEFAULT 'single',
            files        TEXT DEFAULT '[]',
            hosted_url   TEXT DEFAULT NULL,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS snippets (
            id         TEXT PRIMARY KEY,
            language   TEXT,
            code       TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

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
            published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS post_likes (
            user_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            PRIMARY KEY(user_id, post_id)
        );
    """)

    # Migrations for existing databases
    try:
        cur.execute("ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT 'single'")
    except: pass
    try:
        cur.execute("ALTER TABLE projects ADD COLUMN files TEXT DEFAULT '[]'")
    except: pass
    try:
        cur.execute("ALTER TABLE projects ADD COLUMN hosted_url TEXT DEFAULT NULL")
    except: pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
    except: pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
    except: pass
    try:
        cur.execute("UPDATE projects SET project_type='single' WHERE project_type IS NULL")
    except: pass

    con.commit()
    con.close()

init_db()

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

def optional_jwt_identity():
    """Return user_id if JWT present, else None."""
    try:
        verify_jwt_in_request(optional=True)
        return get_jwt_identity()
    except:
        return None

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
    con.execute(
        "INSERT OR REPLACE INTO verification_codes (email, code, expires_at) VALUES (?,?,?)",
        (email, code, expires)
    )
    con.commit()
    con.close()

    try:
        resend.Emails.send({
            "from": "CodeSponge <onboarding@resend.dev>",
            "to":   [email],
            "subject": "Your CodeSponge verification code",
            "text": f"Your verification code is: {code}\n\nIt expires in 10 minutes."
        })
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

    existing = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if existing:
        con.close()
        return jsonify({"error": "An account with this email already exists"}), 400

    user_id  = str(uuid.uuid4())
    username = email.split("@")[0]
    con.execute(
        "INSERT INTO users (id, email, password, username, verified) VALUES (?,?,?,?,1)",
        (user_id, email, generate_password_hash(password), username)
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

    con  = get_db()
    user = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    con.close()

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401

    token = create_access_token(identity=user["id"])
    return jsonify({"token": token, "email": email})

# ── Projects: List ────────────────────────────────────────────────────────────
@app.route("/projects", methods=["GET"])
@jwt_required()
def list_projects():
    user_id = get_jwt_identity()
    con  = get_db()
    rows = con.execute(
        "SELECT id, name, language, project_type, hosted_url, updated_at FROM projects WHERE user_id=? ORDER BY updated_at DESC",
        (user_id,)
    ).fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

# ── Projects: Create ──────────────────────────────────────────────────────────
@app.route("/projects", methods=["POST"])
@jwt_required()
def create_project():
    user_id = get_jwt_identity()
    data    = request.json
    name    = (data.get("name") or "Untitled").strip()
    ptype   = data.get("project_type", "single")
    lang    = data.get("language", "python") if ptype == "single" else "html"
    proj_id = str(uuid.uuid4())[:8]

    # Default files for multi/website
    if ptype == "website":
        files = json.dumps([
            {"id": "f1", "name": "index.html",  "content": get_default_html(), "language": "html"},
            {"id": "f2", "name": "style.css",   "content": get_default_css(),  "language": "css"},
            {"id": "f3", "name": "script.js",   "content": "// script.js\nconsole.log('Hello!');", "language": "javascript"},
        ])
        code = ""
    elif ptype == "multi":
        files = json.dumps([
            {"id": "f1", "name": "main.py", "content": get_default_code("python"), "language": "python"},
        ])
        code = ""
    else:
        files = "[]"
        code  = get_default_code(lang)

    con = get_db()
    con.execute(
        "INSERT INTO projects (id, user_id, name, language, code, project_type, files) VALUES (?,?,?,?,?,?,?)",
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
    row = con.execute(
        "SELECT * FROM projects WHERE id=? AND user_id=?", (proj_id, user_id)
    ).fetchone()
    con.close()
    if not row:
        return jsonify({"error": "Project not found"}), 404
    d = dict(row)
    # Parse files JSON
    try:
        d["files"] = json.loads(d.get("files") or "[]")
    except:
        d["files"] = []
    return jsonify(d)

# ── Projects: Update ──────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>", methods=["PUT"])
@jwt_required()
def update_project(proj_id):
    user_id = get_jwt_identity()
    data    = request.json
    con     = get_db()

    # Build update dynamically
    fields = []
    values = []

    if "name" in data:
        fields.append("name=?"); values.append(data["name"])
    if "code" in data:
        fields.append("code=?"); values.append(data.get("code", ""))
    if "language" in data:
        fields.append("language=?"); values.append(data.get("language", "python"))
    if "files" in data:
        fields.append("files=?"); values.append(json.dumps(data["files"]))
    if "hosted_url" in data:
        fields.append("hosted_url=?"); values.append(data.get("hosted_url"))

    fields.append("updated_at=?"); values.append(datetime.utcnow())
    values.extend([proj_id, user_id])

    con.execute(
        f"UPDATE projects SET {', '.join(fields)} WHERE id=? AND user_id=?",
        values
    )
    con.commit()
    con.close()
    return jsonify({"message": "Saved"})

# ── Projects: Delete ──────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>", methods=["DELETE"])
@jwt_required()
def delete_project(proj_id):
    user_id = get_jwt_identity()
    con = get_db()
    con.execute("DELETE FROM projects WHERE id=? AND user_id=?", (proj_id, user_id))
    con.commit()
    con.close()
    return jsonify({"message": "Deleted"})

# ── Website Hosting ───────────────────────────────────────────────────────────
@app.route("/projects/<proj_id>/host", methods=["POST"])
@jwt_required()
def host_project(proj_id):
    user_id = get_jwt_identity()
    data    = request.json
    files   = data.get("files", [])

    # Verify ownership
    con = get_db()
    row = con.execute("SELECT id FROM projects WHERE id=? AND user_id=?", (proj_id, user_id)).fetchone()
    if not row:
        con.close()
        return jsonify({"error": "Project not found"}), 404

    # Write files to sites directory
    site_dir = os.path.join(SITES_DIR, proj_id)
    os.makedirs(site_dir, exist_ok=True)

    for f in files:
        fname   = f.get("name", "index.html")
        content = f.get("content", "")
        # Security: prevent path traversal
        safe_name = os.path.basename(fname)
        with open(os.path.join(site_dir, safe_name), "w", encoding="utf-8") as fp:
            fp.write(content)

    # Build public URL
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://codesponge-backend.onrender.com")
    url      = f"{base_url}/sites/{proj_id}/index.html"

    # Save hosted_url to project
    con.execute(
        "UPDATE projects SET hosted_url=?, updated_at=? WHERE id=?",
        (url, datetime.utcnow(), proj_id)
    )
    con.commit()
    con.close()

    return jsonify({"url": url})

# Serve hosted sites
@app.route("/sites/<proj_id>/<path:filename>")
def serve_site(proj_id, filename):
    safe_name = os.path.basename(filename)
    site_dir  = os.path.join(SITES_DIR, proj_id)
    return send_from_directory(site_dir, safe_name)

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

# ── Snippets ──────────────────────────────────────────────────────────────────
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

# ── Community: List posts ─────────────────────────────────────────────────────
@app.route("/community", methods=["GET"])
def list_community():
    sort   = request.args.get("sort", "recent")   # recent | trending | top
    lang   = request.args.get("language", "")
    search = request.args.get("q", "").strip()
    limit  = min(int(request.args.get("limit", 20)), 50)
    offset = int(request.args.get("offset", 0))

    viewer_id = optional_jwt_identity()

    order = {
        "trending": "cp.views DESC, cp.likes DESC",
        "top":      "cp.likes DESC",
        "recent":   "cp.published_at DESC",
    }.get(sort, "cp.published_at DESC")

    where_clauses = []
    params        = []

    if lang:
        where_clauses.append("cp.language=?")
        params.append(lang)
    if search:
        where_clauses.append("(cp.title LIKE ? OR cp.description LIKE ? OR u.username LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    con  = get_db()
    rows = con.execute(f"""
        SELECT cp.id, cp.title, cp.description, cp.language, cp.project_type,
               cp.code, cp.files, cp.hosted_url,
               cp.likes, cp.forks, cp.views, cp.published_at,
               u.username, u.email,
               CASE WHEN pl.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked_by_me
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        LEFT JOIN post_likes pl ON pl.post_id = cp.id AND pl.user_id = ?
        {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """, [viewer_id or "", *params, limit, offset]).fetchall()

    total = con.execute(f"""
        SELECT COUNT(*) FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        {where_sql}
    """, params).fetchone()[0]

    con.close()

    result = []
    for r in rows:
        d = dict(r)
        # Truncate code preview
        code = d.get("code") or ""
        try:
            files = json.loads(d.get("files") or "[]")
            if files and not code:
                code = files[0].get("content", "")
        except:
            files = []
        d["code_preview"] = code[:400]
        d["files"]        = files
        del d["code"]
        result.append(d)

    return jsonify({"posts": result, "total": total})

# ── Community: Publish project ────────────────────────────────────────────────
@app.route("/community/publish", methods=["POST"])
@jwt_required()
def publish_project():
    user_id = get_jwt_identity()
    data    = request.json

    proj_id     = data.get("project_id")
    title       = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not title:
        return jsonify({"error": "Title is required"}), 400

    # Load project if project_id given
    code  = data.get("code", "")
    files = data.get("files", [])
    lang  = data.get("language", "")
    ptype = data.get("project_type", "single")
    hosted_url = data.get("hosted_url")

    if proj_id:
        con = get_db()
        row = con.execute("SELECT * FROM projects WHERE id=? AND user_id=?", (proj_id, user_id)).fetchone()
        con.close()
        if row:
            p     = dict(row)
            code  = p.get("code", "")
            lang  = p.get("language", "")
            ptype = p.get("project_type", "single")
            hosted_url = p.get("hosted_url")
            try:
                files = json.loads(p.get("files") or "[]")
            except:
                files = []

    post_id = str(uuid.uuid4())[:8]
    con     = get_db()
    con.execute("""
        INSERT INTO community_posts
        (id, user_id, project_id, title, description, language, project_type, code, files, hosted_url)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (post_id, user_id, proj_id, title, description, lang, ptype,
          code, json.dumps(files), hosted_url))
    con.commit()
    con.close()

    return jsonify({"id": post_id, "message": "Published!"})

# ── Community: Get single post ────────────────────────────────────────────────
@app.route("/community/<post_id>", methods=["GET"])
def get_post(post_id):
    viewer_id = optional_jwt_identity()
    con  = get_db()

    # Increment view count
    con.execute("UPDATE community_posts SET views = views + 1 WHERE id=?", (post_id,))
    con.commit()

    row = con.execute("""
        SELECT cp.*, u.username, u.email, u.bio,
               CASE WHEN pl.user_id IS NOT NULL THEN 1 ELSE 0 END AS liked_by_me
        FROM community_posts cp
        JOIN users u ON cp.user_id = u.id
        LEFT JOIN post_likes pl ON pl.post_id = cp.id AND pl.user_id = ?
        WHERE cp.id=?
    """, (viewer_id or "", post_id)).fetchone()
    con.close()

    if not row:
        return jsonify({"error": "Post not found"}), 404

    d = dict(row)
    try:
        d["files"] = json.loads(d.get("files") or "[]")
    except:
        d["files"] = []
    return jsonify(d)

# ── Community: Like / Unlike ──────────────────────────────────────────────────
@app.route("/community/<post_id>/like", methods=["POST"])
@jwt_required()
def like_post(post_id):
    user_id = get_jwt_identity()
    con     = get_db()

    existing = con.execute(
        "SELECT 1 FROM post_likes WHERE user_id=? AND post_id=?", (user_id, post_id)
    ).fetchone()

    if existing:
        con.execute("DELETE FROM post_likes WHERE user_id=? AND post_id=?", (user_id, post_id))
        con.execute("UPDATE community_posts SET likes = MAX(0, likes-1) WHERE id=?", (post_id,))
        liked = False
    else:
        con.execute("INSERT INTO post_likes (user_id, post_id) VALUES (?,?)", (user_id, post_id))
        con.execute("UPDATE community_posts SET likes = likes+1 WHERE id=?", (post_id,))
        liked = True

    likes = con.execute("SELECT likes FROM community_posts WHERE id=?", (post_id,)).fetchone()["likes"]
    con.commit()
    con.close()
    return jsonify({"liked": liked, "likes": likes})

# ── Community: Fork ───────────────────────────────────────────────────────────
@app.route("/community/<post_id>/fork", methods=["POST"])
@jwt_required()
def fork_post(post_id):
    user_id = get_jwt_identity()
    con     = get_db()

    post = con.execute("SELECT * FROM community_posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        con.close()
        return jsonify({"error": "Post not found"}), 404

    p       = dict(post)
    proj_id = str(uuid.uuid4())[:8]
    name    = "Fork of " + p["title"]

    con.execute(
        "INSERT INTO projects (id, user_id, name, language, code, project_type, files) VALUES (?,?,?,?,?,?,?)",
        (proj_id, user_id, name, p.get("language","python"),
         p.get("code",""), p.get("project_type","single"), p.get("files","[]"))
    )
    con.execute("UPDATE community_posts SET forks = forks+1 WHERE id=?", (post_id,))
    con.commit()
    con.close()

    return jsonify({
        "project_id":   proj_id,
        "project_type": p.get("project_type", "single"),
        "message":      "Forked!"
    })

# ── Community: Delete post ────────────────────────────────────────────────────
@app.route("/community/<post_id>", methods=["DELETE"])
@jwt_required()
def delete_post(post_id):
    user_id = get_jwt_identity()
    con     = get_db()
    con.execute("DELETE FROM community_posts WHERE id=? AND user_id=?", (post_id, user_id))
    con.execute("DELETE FROM post_likes WHERE post_id=?", (post_id,))
    con.commit()
    con.close()
    return jsonify({"message": "Deleted"})

# ── User profile ──────────────────────────────────────────────────────────────
@app.route("/users/<username>", methods=["GET"])
def get_profile(username):
    con  = get_db()
    user = con.execute(
        "SELECT id, username, email, bio, created_at FROM users WHERE username=?", (username,)
    ).fetchone()
    if not user:
        con.close()
        return jsonify({"error": "User not found"}), 404

    posts = con.execute("""
        SELECT id, title, description, language, project_type, hosted_url,
               likes, forks, views, published_at
        FROM community_posts WHERE user_id=? ORDER BY published_at DESC
    """, (user["id"],)).fetchall()
    con.close()

    return jsonify({
        "username":   user["username"],
        "email":      user["email"],
        "bio":        user["bio"],
        "created_at": user["created_at"],
        "posts":      [dict(p) for p in posts],
    })

# ── Default content helpers ───────────────────────────────────────────────────
def get_default_html():
    return '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>My Website</title>
  <link rel="stylesheet" href="style.css"/>
</head>
<body>
  <h1>Hello, World!</h1>
  <p>Edit index.html, style.css and script.js to build your site.</p>
  <script src="script.js"></script>
</body>
</html>'''

def get_default_css():
    return '''*, *::before, *::after { box-sizing: border-box; }
body {
  font-family: sans-serif;
  margin: 0; padding: 40px;
  background: #0d1117;
  color: #e6edf3;
}
h1 { font-size: 2rem; margin-bottom: 12px; }'''

if __name__ == "__main__":
    app.run(debug=True)
