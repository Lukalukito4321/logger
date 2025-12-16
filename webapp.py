
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, request, session, abort, jsonify, render_template_string

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://127.0.0.1:5000/callback")

BOT_API_KEY = os.getenv("BOT_API_KEY", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

DISCORD_API = "https://discord.com/api"
OAUTH_SCOPE = "identify guilds"
DB_PATH = BASE_DIR / "settings.db"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
          guild_id TEXT PRIMARY KEY,
          log_channel_id TEXT DEFAULT '',
          log_join INTEGER DEFAULT 1,
          log_invites INTEGER DEFAULT 1,
          log_nickname INTEGER DEFAULT 1,
          log_roles INTEGER DEFAULT 1,
          log_message_edit INTEGER DEFAULT 1,
          log_message_delete INTEGER DEFAULT 1,
          log_ban INTEGER DEFAULT 1,
          log_kick INTEGER DEFAULT 1,
          log_timeout INTEGER DEFAULT 1
        )
        """
    )
    conn.commit()
    conn.close()

init_db()

def discord_get(path):
    r = requests.get(
        f"{DISCORD_API}{path}",
        headers={"Authorization": f"Bearer {session.get('access_token', '')}"}
    )
    try:
        return r.json(), r.status_code
    except Exception:
        return {"error": "bad_json"}, r.status_code


def manageable_guilds():
    data, status = discord_get("/users/@me/guilds")

    # თუ token არასწორია/expired ან error დაბრუნდა
    if status != 200:
        return [], [], f"Discord API error {status}: {data}"

    if not isinstance(data, list):
        return [], [], f"Unexpected response: {data}"

    manageable, others = [], []
    for g in data:
        if not isinstance(g, dict):
            continue
        perms = int(g.get("permissions", 0))
        if perms & 0x20:  # Manage Server
            manageable.append(g)
        else:
            others.append(g)

    return manageable, others, None


BASE_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{{ title }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #0b1220; color: #e9eefc; }
    .card { background: #101b33; border: 1px solid #1d2a4d; }
    .muted { color: #a9b6d6; }
    a { color: #8ab4ff; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .btn-primary { background: #2b6cff; border: none; }
    .btn-primary:hover { background: #1f56d6; }
    .badge-soft { background: rgba(138,180,255,.15); color:#8ab4ff; border:1px solid rgba(138,180,255,.25); }
    .list-group-item { background: transparent; }
  </style>
</head>
<body>
  <div class="container py-4">
    <div class="d-flex align-items-center justify-content-between mb-4">
      <div>
        <h3 class="mb-0">Logger Dashboard</h3>
        <div class="muted">Choose where logs go and what to log.</div>
      </div>
      <div>
        {% if logged_in %}
          <a class="btn btn-outline-light btn-sm" href="/logout">Logout</a>
        {% endif %}
      </div>
    </div>

    {{ body|safe }}
  </div>
</body>
</html>
"""

def page(title, body, logged_in=False):
    return render_template_string(BASE_HTML, title=title, body=body, logged_in=logged_in)

def checkbox(name, label, checked):
    return f"""
    <div class="col-sm-6 col-md-4">
      <div class="form-check">
        <input class="form-check-input" type="checkbox" name="{name}" id="{name}" {"checked" if checked else ""}>
        <label class="form-check-label" for="{name}">{label}</label>
      </div>
    </div>
    """

@app.get("/")
def home():
    body = """
    <div class="card p-4">
      <h5 class="mb-2">Login</h5>
      <div class="muted mb-3">Sign in with Discord to manage logging settings for servers you can manage.</div>
      <a class="btn btn-primary" href="/login">Login with Discord</a>
    </div>
    """
    return page("Logger Dashboard", body, logged_in=("access_token" in session))

@app.get("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return page("Config error", "<div class='alert alert-danger'>Missing DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET in web/.env</div>")
    url = (
        f"{DISCORD_API}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={OAUTH_SCOPE.replace(' ', '%20')}"
    )
    return redirect(url)

@app.get("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.get("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return page("OAuth error", "<div class='alert alert-danger'>No code returned from Discord.</div>")

    token = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": OAUTH_SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    ).json()

    if "access_token" not in token:
        return page("OAuth failed", f"<div class='alert alert-danger'>OAuth failed: {token}</div>")

    session["access_token"] = token["access_token"]
    return redirect("/guilds")

@app.get("/guilds")
def guilds():
    if "access_token" not in session:
        return redirect("/login")

    manageable, others, err = manageable_guilds()
    if err:
        return f"<h3>Failed to load guilds</h3><pre>{err}</pre>", 500

    # --- Invite URL for adding the bot ---
    # Make sure you have BOT_CLIENT_ID in your .env (this is the Application ID of the bot)
    client_id = os.getenv("BOT_CLIENT_ID") or os.getenv("DISCORD_CLIENT_ID")  # fallback if you reuse same app
    if not client_id:
        return "<h3>Missing BOT_CLIENT_ID (Application ID) in web/.env</h3>", 500

    perms = os.getenv("BOT_PERMISSIONS", "8")  # 8 = Administrator (change if you want)
    add_bot_url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        "&scope=bot%20applications.commands"
        f"&permissions={perms}"
    )

    # --- Build HTML lists ---
    manageable_html = ""
    for g in manageable:
        gid = g.get("id")
        name = (g.get("name") or "Unknown").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        manageable_html += f"""
          <a class="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
             href="/dashboard/{gid}">
            <span>{name}</span>
            <span class="badge badge-soft">Configure</span>
          </a>
        """

    if not manageable_html:
        manageable_html = """
          <div class="text-muted">
            No manageable servers found. You need <b>Manage Server</b> permission.
          </div>
        """

    others_html = ""
    for g in others:
        name = (g.get("name") or "Unknown").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        others_html += f"<li>{name}</li>"
    if not others_html:
        others_html = "<li>None</li>"

    # --- Page body (your design) ---
    body = f"""
    <div class="row g-3">
      <div class="col-lg-8">
        <div class="card p-4">
          <div class="d-flex align-items-center justify-content-between">
            <h5 class="mb-0">Servers you can manage</h5>
            <a class="btn btn-sm btn-primary" href="{add_bot_url}" target="_blank">Add bot to a server</a>
          </div>
          <div class="muted mt-1">
            Only servers where you have <span class="badge badge-soft">Manage Server</span> can be configured.
          </div>
          <hr class="border-secondary"/>
          <div class="list-group list-group-flush">
            {manageable_html}
          </div>
        </div>
      </div>

      <div class="col-lg-4">
        <div class="card p-4">
          <h6 class="mb-2">Other servers</h6>
          <div class="muted mb-2">You're in these servers, but you can't configure them.</div>
          <ul class="mb-0 muted">
            {others_html}
          </ul>
        </div>
      </div>
    </div>
    """

    # If you have a layout/template helper, use it. If not, return body directly.
    # Example: return render_page("Guilds", body)
    return render_template_string(BASE_TEMPLATE, title="Guilds", body=body)

BASE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background:#0b1220; color:#e5e7eb; }
    .card { background:#111a2e; border:1px solid rgba(255,255,255,.08); border-radius:16px; }
    .muted { color: rgba(229,231,235,.7); }
    .badge-soft { background: rgba(99,102,241,.15); color:#c7d2fe; border:1px solid rgba(99,102,241,.35); }
    .list-group-item { background: transparent; color:#e5e7eb; border-color: rgba(255,255,255,.08); }
    .list-group-item:hover { background: rgba(255,255,255,.04); }
  </style>
  <title>{{title}}</title>
</head>
<body class="py-4">
  <div class="container">
    <h2 class="mb-1">Logger Dashboard</h2>
    <div class="muted mb-4">Choose where logs go and what to log.</div>
    {{body|safe}}
  </div>
</body>
</html>
"""


@app.get("/dashboard/<guild_id>/channels")
def dashboard_channels(guild_id):
    if "access_token" not in session:
        abort(401)

    manageable, _ = manageable_guilds()
    if str(guild_id) not in {str(g["id"]) for g in manageable}:
        abort(403)

    if not DISCORD_BOT_TOKEN:
        return jsonify([])

    r = requests.get(
        f"{DISCORD_API}/guilds/{guild_id}/channels",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
        timeout=15,
    )
    if r.status_code != 200:
        return jsonify([])

    chans = [{"id": str(c["id"]), "name": c["name"]} for c in r.json() if c.get("type") == 0]
    chans.sort(key=lambda x: x["name"].lower())
    return jsonify(chans)

@app.route("/dashboard/<guild_id>", methods=["GET", "POST"])
def dashboard(guild_id):
    if "access_token" not in session:
        return redirect("/login")

    # --- ensure guild_id is string ---
    guild_id = str(guild_id)

    conn = db()
    row = conn.execute(
        "SELECT * FROM guild_settings WHERE guild_id=?",
        (guild_id,)
    ).fetchone()

    # --- if no row yet, create default ---
    if row is None:
        conn.execute(
            "INSERT INTO guild_settings (guild_id) VALUES (?)",
            (guild_id,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM guild_settings WHERE guild_id=?",
            (guild_id,)
        ).fetchone()

    # --- handle POST (save settings) ---
    if request.method == "POST":
        def b(name): return 1 if request.form.get(name) == "on" else 0

        conn.execute("""
            UPDATE guild_settings SET
              log_channel_id=?,
              log_join=?, log_invites=?, log_nickname=?, log_roles=?,
              log_message_edit=?, log_message_delete=?,
              log_ban=?, log_kick=?, log_timeout=?
            WHERE guild_id=?
        """, (
            request.form.get("log_channel_id", ""),
            b("log_join"), b("log_invites"), b("log_nickname"), b("log_roles"),
            b("log_message_edit"), b("log_message_delete"),
            b("log_ban"), b("log_kick"), b("log_timeout"),
            guild_id
        ))
        conn.commit()

        # reload row after save
        row = conn.execute(
            "SELECT * FROM guild_settings WHERE guild_id=?",
            (guild_id,)
        ).fetchone()

    conn.close()

    # --- SAFE rendering (no missing keys) ---
    body = f"""
    <div class="card p-4">
      <h5 class="mb-3">Logger settings</h5>

      <form method="post">
        <div class="mb-3">
          <label class="form-label">Log channel ID</label>
          <input class="form-control" name="log_channel_id"
                 value="{row['log_channel_id'] or ''}">
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_join" {'checked' if row['log_join'] else ''}>
          <label class="form-check-label">Member Join / Leave</label>
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_nickname" {'checked' if row['log_nickname'] else ''}>
          <label class="form-check-label">Nickname changes</label>
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_roles" {'checked' if row['log_roles'] else ''}>
          <label class="form-check-label">Role changes</label>
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_message_delete" {'checked' if row['log_message_delete'] else ''}>
          <label class="form-check-label">Message delete</label>
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_message_edit" {'checked' if row['log_message_edit'] else ''}>
          <label class="form-check-label">Message edit</label>
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_ban" {'checked' if row['log_ban'] else ''}>
          <label class="form-check-label">Ban</label>
        </div>

        <div class="form-check">
          <input class="form-check-input" type="checkbox" name="log_kick" {'checked' if row['log_kick'] else ''}>
          <label class="form-check-label">Kick</label>
        </div>

        <div class="form-check mb-3">
          <input class="form-check-input" type="checkbox" name="log_timeout" {'checked' if row['log_timeout'] else ''}>
          <label class="form-check-label">Timeout</label>
        </div>

        <button class="btn btn-primary">Save settings</button>
      </form>
    </div>
    """

    return render_template_string(
        BASE_TEMPLATE,
        title="Dashboard",
        body=body
    )


@app.get("/api/settings/<guild_id>")
def api_settings(guild_id):
    if request.headers.get("X-API-KEY") != BOT_API_KEY:
        abort(401)

    conn = db()
    row = conn.execute("SELECT * FROM guild_settings WHERE guild_id=?", (str(guild_id),)).fetchone()
    conn.close()
    if not row:
        return {"guild_id": str(guild_id)}, 404
    return dict(row)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))  # Railway provides PORT
    app.run(host="0.0.0.0", port=port, debug=False)
