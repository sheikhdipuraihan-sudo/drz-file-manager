"""
DRZ File Manager - Ultra Enhanced UI Edition
Features: modern UI, SVG icon system, landing page, dark mode, drag-drop upload,
file previews, analytics, live monitor, filterable audit log, toasts, animations.
Run:  pip install flask  &&  python drz_file_manager.py   ->  http://127.0.0.1:5000
"""
from flask import (Flask, render_template_string, request, redirect, url_for,
                   flash, session, send_file, abort, g, jsonify)
import os, json, shutil, uuid, secrets, time
from datetime import datetime, timedelta, timezone
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from markupsafe import Markup
import sqlite3

BASE_DIR = '/tmp/drz-file-manager' if os.environ.get('VERCEL') else os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE_DIR, exist_ok=True)
app = Flask(__name__)

def _load_secret():
    env = os.environ.get('SECRET_KEY')
    if env:
        return env
    path = os.path.join(BASE_DIR, '.secret_key')
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(key)
    return key

app.secret_key = _load_secret()
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FORCE_HTTPS_COOKIES', '0') == '1'

SESSION_TIMEOUT = 30
PIN_VALID_MINUTES = 10
ONLINE_WINDOW = 5 * 60
DATABASE = os.path.join(BASE_DIR, 'filemanager.db')

_login_attempts, _pin_attempts = {}, {}
RATE_LIMIT_WINDOW = 15 * 60
RATE_LIMIT_MAX = 5

def _rate_limited(store, key):
    now = time.time()
    attempts = [t for t in store.get(key, []) if now - t < RATE_LIMIT_WINDOW]
    store[key] = attempts
    return len(attempts) >= RATE_LIMIT_MAX

def _record_failure(store, key):
    store.setdefault(key, []).append(time.time())

def now_str(dt=None):
    return (dt or datetime.now(timezone.utc)).strftime('%Y-%m-%d %H:%M:%S')

# ============================================================
# SVG ICON SYSTEM
# ============================================================
ICONS = {
    # navigation / UI
    'files':         '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    'trash':         '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    'user':          '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    'shield':        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    'moon':          '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    'sun':           '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    'logout':        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    'menu':          '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
    'x':             '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'upload':        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    'download':      '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    'share':         '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>',
    'link':          '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    'restore':       '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    'lock':          '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'key':           '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777z"/>',
    'database':      '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    'bell':          '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    'activity':      '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    'pie':           '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    'plus':          '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    'users':         '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    'satellite':     '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/>',
    'list':          '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    'alert':         '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    'search':        '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    'filter':        '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    'check':         '<polyline points="20 6 9 17 4 12"/>',
    'globe':         '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    'cloud':         '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
    'eye':           '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    'refresh':       '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    'zap':           '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    'heart':         '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    # file types
    'file':          '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>',
    'file-text':     '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/>',
    'file-pdf':      '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="9" y2="17"/><line x1="12" y1="13" x2="15" y2="13"/><line x1="12" y1="17" x2="15" y2="17"/>',
    'image':         '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    'video':         '<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
    'audio':         '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    'spreadsheet':   '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="12" y1="9" x2="12" y2="21"/>',
    'presentation':  '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    'archive':       '<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
    'code':          '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    'palette':       '<circle cx="12" cy="12" r="10"/><circle cx="8" cy="8" r="1"/><circle cx="12" cy="6" r="1"/><circle cx="16" cy="8" r="1"/><circle cx="6" cy="12" r="1"/><circle cx="8" cy="16" r="1"/>',
    'settings':      '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    'type':          '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
    'json':          '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M10 12l-2 2 2 2"/><path d="M14 12l2 2-2 2"/>',
}

def icon(name, size=20, cls=''):
    path = ICONS.get(name, ICONS['file'])
    return Markup(
        f'<svg class="icon {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )

FILE_ICON_MAP = {
    'jpg':'image','jpeg':'image','png':'image','gif':'image','svg':'image','webp':'image','bmp':'image','ico':'image',
    'mp4':'video','mov':'video','avi':'video','mkv':'video','webm':'video',
    'mp3':'audio','wav':'audio','flac':'audio','ogg':'audio','m4a':'audio',
    'pdf':'file-pdf','doc':'file-text','docx':'file-text','txt':'file-text','md':'file-text','rtf':'file-text',
    'xls':'spreadsheet','xlsx':'spreadsheet','csv':'spreadsheet','ppt':'presentation','pptx':'presentation',
    'zip':'archive','rar':'archive','7z':'archive','tar':'archive','gz':'archive',
    'exe':'settings','sh':'settings','py':'code','js':'code','html':'globe','css':'palette',
    'json':'json','xml':'json','yml':'json','yaml':'json',
    'ttf':'type','woff':'type','woff2':'type',
}

def file_icon(name, size=20):
    ext = name.rsplit('.', 1)[-1].lower() if name and '.' in name else ''
    return icon(FILE_ICON_MAP.get(ext, 'file'), size)

def is_image(name):
    ext = name.rsplit('.', 1)[-1].lower() if name and '.' in name else ''
    return ext in ('jpg','jpeg','png','gif','svg','webp','bmp','ico')

app.jinja_env.globals['icon'] = icon
app.jinja_env.globals['file_icon'] = file_icon
app.jinja_env.globals['is_image'] = is_image

# ------------------ Database ------------------
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                profile_pic TEXT DEFAULT '',
                last_seen TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT,
                file_size INTEGER DEFAULT 0,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS trash (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_path TEXT NOT NULL,
                deleted_by INTEGER NOT NULL,
                original_name TEXT,
                file_size INTEGER DEFAULT 0,
                deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (deleted_by) REFERENCES users (id)
            );
            CREATE TABLE IF NOT EXISTS share_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                file_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users (id),
                FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                username TEXT, ip_address TEXT, user_agent TEXT,
                action TEXT, status TEXT, failure_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_pin_hash', '');
            INSERT OR IGNORE INTO settings (key, value) VALUES ('pin_history', '[]');
        """)
        db.commit()

def create_default_admin():
    with app.app_context():
        db = get_db()
        if not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            db.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,1)",
                       ('admin', generate_password_hash('00000000')))
            db.commit()
            print("=" * 60)
            print(" Default admin created -> username: admin  password: 00000000")
            print(" CHANGE THIS PASSWORD after first login.")
            print("=" * 60)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
init_db()
create_default_admin()

# ------------------ CSRF ------------------
def get_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = get_csrf_token

@app.before_request
def csrf_protect():
    if request.method == 'POST':
        token = session.get('_csrf_token')
        sent = request.form.get('_csrf_token', '') or request.headers.get('X-CSRF-Token', '')
        if not token or not sent or not secrets.compare_digest(token.encode(), sent.encode()):
            abort(400, description="Invalid or missing CSRF token.")

# ------------------ Helpers ------------------
def get_user_dir(user_id):
    path = os.path.join(app.config['UPLOAD_FOLDER'], f'user_{int(user_id)}')
    os.makedirs(path, exist_ok=True)
    return path

def get_trash_dir(user_id):
    path = os.path.join(get_user_dir(user_id), '.trash')
    os.makedirs(path, exist_ok=True)
    return path

def format_size(num_bytes):
    if not num_bytes:
        return "0 B"
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

app.jinja_env.filters['filesize'] = format_size

def client_ip():
    return request.remote_addr or '-'

def audit(action, status='ok', reason='', username=None):
    u = username or (g.user['username'] if getattr(g, 'user', None) else '-')
    db = get_db()
    db.execute("INSERT INTO audit_logs (timestamp, username, ip_address, user_agent, action, status, failure_reason)"
               " VALUES (?,?,?,?,?,?,?)",
               (now_str(), u, client_ip(), (request.headers.get('User-Agent') or '')[:200],
                action, status, reason))
    db.commit()

@app.before_request
def load_user():
    g.user = None
    if request.endpoint == 'static':
        return
    uid = session.get('user_id')
    if not uid:
        return
    last = session.get('last_active', 0)
    if time.time() - last > SESSION_TIMEOUT * 60:
        session.clear()
        flash("Session expired due to inactivity.", "warn")
        return
    user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        session.clear()
        return
    g.user = user
    if request.endpoint != 'admin_monitor_data':
        session['last_active'] = time.time()
        if time.time() - session.get('seen_written', 0) > 30:
            get_db().execute("UPDATE users SET last_seen=? WHERE id=?", (now_str(), uid))
            get_db().commit()
            session['seen_written'] = time.time()

def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            return redirect('/login?next=' + (request.environ.get('ORIGINAL_PATH') or '/'))
        return f(*a, **kw)
    return wrapper

def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if not g.user['is_admin']:
            abort(403)
        return f(*a, **kw)
    return wrapper

def pin_required(f):
    @wraps(f)
    @admin_required
    def wrapper(*a, **kw):
        if time.time() - session.get('pin_ok_at', 0) > PIN_VALID_MINUTES * 60:
            return redirect(url_for('admin_pin', next=(request.environ.get('ORIGINAL_PATH') or '/')))
        return f(*a, **kw)
    return wrapper

def get_setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row['value'] if row else ''

def set_setting(key, value):
    get_db().execute("INSERT INTO settings (key,value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    get_db().commit()

def safe_next(url):
    return url if url and url.startswith('/') and not url.startswith('//') else url_for('index')

def valid_new_password(pw):
    return len(pw) >= 8

# ============================================================
# THEME / BASE TEMPLATE
# ============================================================
BASE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ page_title or 'DRZ File Manager' }}</title>
<script>(function(){try{var t=localStorage.getItem('drz-theme')||'light';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>
<style>
:root{
  --bg:#f5f7fb; --bg-alt:#eef1f8; --surface:#ffffff; --surface-2:#f8fafc;
  --text:#0f172a; --muted:#64748b; --border:#e2e8f0;
  --primary:#6366f1; --primary-2:#8b5cf6; --primary-glow:rgba(99,102,241,.35);
  --success:#10b981; --danger:#ef4444; --warning:#f59e0b; --info:#0ea5e9;
  --radius:14px; --radius-sm:10px;
  --shadow:0 1px 3px rgba(15,23,42,.06),0 4px 16px rgba(15,23,42,.04);
  --shadow-lg:0 12px 40px rgba(15,23,42,.12);
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,'Inter',sans-serif;
}
[data-theme="dark"]{
  --bg:#0b1120; --bg-alt:#0f172a; --surface:#111827; --surface-2:#1e293b;
  --text:#f1f5f9; --muted:#94a3b8; --border:#1f2937;
  --primary:#818cf8; --primary-2:#a78bfa; --primary-glow:rgba(129,140,248,.35);
  --shadow:0 1px 3px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.3);
  --shadow-lg:0 12px 40px rgba(0,0,0,.6);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  font-family:var(--font);background:var(--bg);color:var(--text);
  min-height:100vh;transition:background .3s ease,color .3s ease;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--primary);text-decoration:none}
a:hover{text-decoration:underline}
/* ---------- icon defaults ---------- */
.icon{display:inline-block;vertical-align:-.15em;flex-shrink:0}
.stat-icon{color:var(--primary)}
.file-icon{color:var(--primary)}
.nav-icon{display:inline-flex;align-items:center;justify-content:center}
.feature-icon{color:var(--primary)}
.dz-icon{color:var(--primary);display:block;margin-bottom:10px}
/* ---------- sidebar ---------- */
.sidebar{
  position:fixed;top:0;left:0;bottom:0;width:248px;
  background:linear-gradient(180deg,var(--surface) 0%,var(--surface-2) 100%);
  border-right:1px solid var(--border);padding:20px 12px;
  display:flex;flex-direction:column;z-index:100;
  transition:transform .3s cubic-bezier(.4,0,.2,1),width .3s;
}
.sidebar.open{transform:translateX(0)}
.brand{display:flex;align-items:center;gap:10px;padding:6px 12px 24px;border-bottom:1px solid var(--border);margin-bottom:12px}
.brand-logo{
  width:38px;height:38px;border-radius:11px;
  background:linear-gradient(135deg,var(--primary),var(--primary-2));
  color:#fff;font-weight:800;font-size:18px;display:grid;place-items:center;
  box-shadow:0 6px 20px var(--primary-glow);
  animation:pop .5s cubic-bezier(.4,1.6,.6,1);
}
@keyframes pop{0%{transform:scale(.5);opacity:0}100%{transform:scale(1);opacity:1}}
.brand-text{font-weight:700;font-size:15px;line-height:1.1}
.brand-text span{display:block;font-size:11px;color:var(--muted);font-weight:500}
.nav{display:flex;flex-direction:column;gap:2px;flex:1}
.nav-item{
  display:flex;align-items:center;gap:12px;padding:11px 14px;
  border-radius:var(--radius-sm);color:var(--muted);font-size:14px;font-weight:500;
  transition:all .2s;text-decoration:none;position:relative;
}
.nav-item:hover{background:var(--bg-alt);color:var(--text);text-decoration:none;transform:translateX(2px)}
.nav-item.active{
  background:linear-gradient(135deg,rgba(99,102,241,.12),rgba(139,92,246,.12));
  color:var(--primary);font-weight:600;
}
.nav-item.active::before{
  content:"";position:absolute;left:-12px;top:50%;transform:translateY(-50%);
  width:3px;height:20px;border-radius:3px;background:var(--primary);
}
.nav-icon{width:20px}
.sidebar-footer{padding-top:12px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:center}
.icon-btn{
  background:var(--bg-alt);border:1px solid var(--border);color:var(--text);
  width:38px;height:38px;border-radius:10px;cursor:pointer;
  display:grid;place-items:center;transition:all .2s;
}
.icon-btn:hover{background:var(--primary);color:#fff;transform:translateY(-2px);box-shadow:0 6px 16px var(--primary-glow)}
.theme-btn .icon-sun{display:none}
[data-theme="dark"] .theme-btn .icon-moon{display:none}
[data-theme="dark"] .theme-btn .icon-sun{display:inline-block}
/* ---------- topbar ---------- */
.topbar{
  position:fixed;top:0;left:248px;right:0;height:64px;z-index:90;
  background:rgba(255,255,255,.7);
  backdrop-filter:saturate(180%) blur(14px);
  -webkit-backdrop-filter:saturate(180%) blur(14px);
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;padding:0 24px;gap:16px;
  transition:left .3s;
}
[data-theme="dark"] .topbar{background:rgba(17,24,39,.7)}
.hamburger{display:none;background:transparent;border:0;cursor:pointer;color:var(--text);padding:6px}
.spacer{flex:1}
.user-chip{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:500}
.user-avatar{
  width:34px;height:34px;border-radius:50%;
  background:linear-gradient(135deg,var(--primary),var(--primary-2));
  color:#fff;display:grid;place-items:center;font-weight:700;font-size:14px;
  box-shadow:0 4px 12px var(--primary-glow);
}
/* ---------- main ---------- */
.main{
  margin-left:248px;padding:88px 24px 40px;
  max-width:1280px;transition:margin-left .3s;
  animation:fadeIn .4s ease;
}
.main-auth{margin-left:0;max-width:none;padding:0;display:grid;place-items:center;min-height:100vh}
.main-landing{margin-left:0;max-width:none;padding:0;min-height:100vh;
  background:radial-gradient(circle at 20% 0%,rgba(99,102,241,.12),transparent 40%),
             radial-gradient(circle at 80% 20%,rgba(139,92,246,.10),transparent 40%);
}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
/* ---------- cards ---------- */
.card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:22px;margin-bottom:20px;box-shadow:var(--shadow);
  animation:slideUp .5s cubic-bezier(.4,0,.2,1) both;
}
@keyframes slideUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.card h3{margin:0 0 16px;font-size:16px;font-weight:600;display:flex;align-items:center;gap:10px}
/* ---------- buttons ---------- */
button, .btn{
  padding:9px 16px;border:0;border-radius:9px;cursor:pointer;
  font-family:inherit;font-size:13px;font-weight:600;
  background:linear-gradient(135deg,var(--primary),var(--primary-2));
  color:#fff;transition:all .2s;position:relative;overflow:hidden;
  box-shadow:0 2px 8px var(--primary-glow);
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
}
button:hover,.btn:hover{transform:translateY(-1px);box-shadow:0 6px 20px var(--primary-glow)}
button:active,.btn:active{transform:translateY(0)}
button.gray{background:var(--bg-alt);color:var(--text);box-shadow:none;border:1px solid var(--border)}
button.gray:hover{background:var(--border);box-shadow:none}
button.red{background:linear-gradient(135deg,#ef4444,#dc2626);box-shadow:0 2px 8px rgba(239,68,68,.35)}
button.small{padding:6px 11px;font-size:12px}
button:disabled{opacity:.5;cursor:not-allowed;transform:none}
/* ---------- inputs ---------- */
input,select,textarea{
  padding:9px 12px;border:1px solid var(--border);border-radius:9px;
  background:var(--surface);color:var(--text);font-family:inherit;font-size:13px;
  transition:all .2s;outline:none;
}
input:focus,select:focus,textarea:focus{
  border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-glow);
}
/* ---------- tables ---------- */
table{width:100%;border-collapse:collapse}
td,th{padding:11px 12px;border-bottom:1px solid var(--border);text-align:left;font-size:13.5px;vertical-align:middle}
th{font-weight:600;color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
tr:last-child td{border-bottom:0}
tbody tr{transition:background .15s}
tbody tr:hover{background:var(--surface-2)}
/* ---------- flash / toast ---------- */
.flash-stack{position:fixed;top:80px;right:24px;z-index:200;display:flex;flex-direction:column;gap:8px;max-width:380px}
.flash,.toast{
  padding:12px 16px;border-radius:11px;font-size:13.5px;font-weight:500;
  box-shadow:var(--shadow-lg);border:1px solid;
  animation:toastIn .4s cubic-bezier(.4,1.4,.6,1);
}
@keyframes toastIn{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:none}}
.flash.ok,.toast-success{background:#dcfce7;color:#166534;border-color:#bbf7d0}
.flash.err,.toast-error{background:#fee2e2;color:#991b1b;border-color:#fecaca}
.flash.warn,.toast-warn{background:#fef3c7;color:#92400e;border-color:#fde68a}
.toast-info{background:#e0f2fe;color:#075985;border-color:#bae6fd}
[data-theme="dark"] .flash.ok,.toast-success{background:#052e16;color:#86efac;border-color:#166534}
[data-theme="dark"] .flash.err,.toast-error{background:#450a0a;color:#fca5a5;border-color:#991b1b}
[data-theme="dark"] .flash.warn,.toast-warn{background:#451a03;color:#fcd34d;border-color:#92400e}
[data-theme="dark"] .toast-info{background:#082f49;color:#7dd3fc;border-color:#075985}
.toasts{position:fixed;top:80px;right:24px;z-index:210;display:flex;flex-direction:column;gap:8px;max-width:380px}
.toast.out{animation:toastOut .3s forwards}
@keyframes toastOut{to{opacity:0;transform:translateX(40px)}}
/* ---------- stat cards ---------- */
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:20px}
.stat-card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:18px 20px;display:flex;align-items:center;gap:14px;
  box-shadow:var(--shadow);transition:all .25s;
  animation:slideUp .5s cubic-bezier(.4,0,.2,1) both;
}
.stat-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-lg);border-color:var(--primary)}
.stat-icon{width:52px;height:52px;border-radius:12px;display:grid;place-items:center;background:var(--bg-alt)}
.stat-body{flex:1;min-width:0}
.stat-value{font-size:22px;font-weight:700;line-height:1.1;margin-bottom:2px}
.stat-label{font-size:12px;color:var(--muted);font-weight:500}
/* ---------- upload drop ---------- */
.drop-zone{
  border:2px dashed var(--border);border-radius:var(--radius);
  padding:40px 20px;text-align:center;cursor:pointer;transition:all .25s;
  background:var(--surface-2);
}
.drop-zone:hover,.drop-zone.drag{
  border-color:var(--primary);background:rgba(99,102,241,.06);
  transform:scale(1.01);
}
.drop-zone.drag{box-shadow:0 0 0 4px var(--primary-glow)}
.drop-zone .dz-title{font-weight:600;margin-bottom:4px}
.drop-zone .dz-sub{color:var(--muted);font-size:13px}
.progress-wrap{height:6px;background:var(--bg-alt);border-radius:99px;overflow:hidden;margin-top:12px;display:none}
.progress-wrap.show{display:block}
.progress-bar{height:100%;width:0;background:linear-gradient(90deg,var(--primary),var(--primary-2));transition:width .2s;border-radius:99px}
/* ---------- file grid ---------- */
.file-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px}
.file-card{
  background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:16px;transition:all .25s;position:relative;overflow:hidden;
  animation:slideUp .4s cubic-bezier(.4,0,.2,1) both;
}
.file-card:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg);border-color:var(--primary)}
.file-icon{text-align:center;margin-bottom:10px;transition:transform .25s}
.file-card:hover .file-icon{transform:scale(1.15) rotate(-4deg)}
.file-thumb{
  width:100%;height:120px;object-fit:cover;border-radius:8px;margin-bottom:10px;
  background:var(--bg-alt);display:block;
}
.file-name{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:4px}
.file-meta{color:var(--muted);font-size:11.5px;margin-bottom:10px}
.file-actions{display:flex;gap:6px;flex-wrap:wrap}
.file-actions form{display:inline}
/* ---------- search bar ---------- */
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.toolbar .spacer{flex:1}
.toolbar form{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
/* ---------- badges ---------- */
.badge{display:inline-block;padding:3px 9px;border-radius:99px;font-size:11px;font-weight:600;letter-spacing:.02em}
.badge-ok{background:#dcfce7;color:#166534}
.badge-fail{background:#fee2e2;color:#991b1b}
.badge-admin{background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff}
.badge-user{background:var(--bg-alt);color:var(--muted)}
[data-theme="dark"] .badge-ok{background:#052e16;color:#86efac}
[data-theme="dark"] .badge-fail{background:#450a0a;color:#fca5a5}
/* ---------- dots ---------- */
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#cbd5e1;margin-right:8px;position:relative}
.dot.on{background:#22c55e;box-shadow:0 0 0 0 rgba(34,197,94,.7);animation:pulse 2s infinite}
@keyframes pulse{
  0%{box-shadow:0 0 0 0 rgba(34,197,94,.6)}
  70%{box-shadow:0 0 0 8px rgba(34,197,94,0)}
  100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}
}
/* ---------- modal ---------- */
.modal-backdrop{
  position:fixed;inset:0;background:rgba(0,0,0,.5);backdrop-filter:blur(4px);
  z-index:300;display:none;align-items:center;justify-content:center;padding:20px;
  animation:fadeIn .2s;
}
.modal-backdrop.show{display:flex}
.modal{
  background:var(--surface);border-radius:var(--radius);max-width:720px;width:100%;
  max-height:90vh;overflow:auto;padding:24px;box-shadow:var(--shadow-lg);
  animation:modalIn .35s cubic-bezier(.4,1.4,.6,1);position:relative;
}
@keyframes modalIn{from{opacity:0;transform:scale(.9) translateY(20px)}to{opacity:1;transform:none}}
.modal-close{position:absolute;top:12px;right:12px;background:transparent;border:0;cursor:pointer;color:var(--muted);padding:6px;box-shadow:none}
.modal-close:hover{background:var(--bg-alt);color:var(--text);transform:none;box-shadow:none}
.modal img{max-width:100%;border-radius:8px;display:block;margin:auto}
/* ---------- charts ---------- */
.chart-bars{display:flex;align-items:flex-end;gap:8px;height:180px;padding:10px 0;border-bottom:1px solid var(--border)}
.bar-wrap{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end;gap:6px}
.bar{
  width:100%;max-width:50px;background:linear-gradient(180deg,var(--primary),var(--primary-2));
  border-radius:6px 6px 0 0;min-height:2px;transition:height .6s cubic-bezier(.4,0,.2,1);
  position:relative;animation:growUp .8s cubic-bezier(.4,0,.2,1);
}
@keyframes growUp{from{height:0}}
.bar:hover::after{
  content:attr(data-count);position:absolute;top:-26px;left:50%;transform:translateX(-50%);
  background:var(--text);color:var(--bg);padding:2px 8px;border-radius:6px;font-size:11px;white-space:nowrap;
}
.bar-label{font-size:11px;color:var(--muted);font-weight:500}
/* ---------- donut ---------- */
.donut-wrap{display:flex;align-items:center;gap:24px;flex-wrap:wrap}
.donut{width:130px;height:130px;transform:rotate(-90deg)}
.donut circle{transition:stroke-dashoffset 1s cubic-bezier(.4,0,.2,1)}
.legend{display:flex;flex-direction:column;gap:6px;font-size:13px}
.legend-item{display:flex;align-items:center;gap:8px}
.legend-dot{width:10px;height:10px;border-radius:3px}
/* ---------- login ---------- */
.auth-bg{
  min-height:100vh;display:grid;place-items:center;padding:24px;
  background:radial-gradient(circle at 20% 20%,rgba(99,102,241,.15),transparent 50%),
             radial-gradient(circle at 80% 80%,rgba(139,92,246,.15),transparent 50%),
             var(--bg);
  animation:fadeIn .5s;
}
.auth-card{
  background:var(--surface);border:1px solid var(--border);border-radius:20px;
  padding:40px 32px;width:100%;max-width:400px;box-shadow:var(--shadow-lg);
  animation:slideUp .5s cubic-bezier(.4,0,.2,1);
}
.auth-card h2{margin:0 0 8px;font-size:24px}
.auth-card .sub{color:var(--muted);font-size:13.5px;margin-bottom:24px}
.auth-card input{width:100%;padding:12px 14px;margin-bottom:12px;font-size:14px}
.auth-card button{width:100%;padding:12px;font-size:14px}
.auth-brand{display:flex;align-items:center;gap:12px;margin-bottom:28px;justify-content:center}
.auth-brand .brand-logo{width:48px;height:48px;font-size:22px}
.auth-brand h1{font-size:17px;margin:0;font-weight:700}
.auth-brand span{display:block;font-size:11.5px;color:var(--muted);font-weight:500}
/* ---------- landing page ---------- */
.landing{display:flex;flex-direction:column;min-height:100vh;width:100%}
.landing-nav{
  display:flex;align-items:center;padding:20px 32px;gap:16px;
  border-bottom:1px solid var(--border);
  background:rgba(255,255,255,.6);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  position:sticky;top:0;z-index:100;
}
[data-theme="dark"] .landing-nav{background:rgba(17,24,39,.6)}
.landing-brand{display:flex;align-items:center;gap:12px;font-weight:700}
.landing-brand .brand-logo{width:40px;height:40px;font-size:18px}
.landing-brand .brand-text{font-size:16px}
.landing-nav-links{margin-left:auto;display:flex;gap:10px;align-items:center}
.landing-nav-links a{text-decoration:none}
.hero{padding:100px 32px 80px;text-align:center;max-width:900px;margin:0 auto;width:100%}
.hero h1{font-size:clamp(32px,5vw,56px);font-weight:800;line-height:1.05;margin:0 0 20px;letter-spacing:-.02em}
.hero h1 .grad{background:linear-gradient(135deg,var(--primary),var(--primary-2));-webkit-background-clip:text;background-clip:text;color:transparent}
.hero-sub{font-size:clamp(15px,1.8vw,19px);color:var(--muted);max-width:640px;margin:0 auto 32px;line-height:1.6}
.hero-actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.hero-actions a{text-decoration:none}
.hero-actions .btn{padding:13px 26px;font-size:14px;border-radius:11px;font-weight:600;box-shadow:0 6px 24px var(--primary-glow)}
.hero-actions .btn-secondary{background:var(--surface);color:var(--text);border:1px solid var(--border);box-shadow:none}
.hero-actions .btn-secondary:hover{background:var(--bg-alt)}
.features{padding:40px 32px 80px;max-width:1200px;margin:0 auto;width:100%}
.features h2{text-align:center;font-size:clamp(24px,3vw,36px);margin:0 0 40px;font-weight:700}
.features-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}
.feature-card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:28px 22px;transition:all .25s;box-shadow:var(--shadow);
}
.feature-card:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg);border-color:var(--primary)}
.feature-icon{
  width:52px;height:52px;border-radius:12px;display:grid;place-items:center;
  background:linear-gradient(135deg,rgba(99,102,241,.14),rgba(139,92,246,.14));
  margin-bottom:16px;
}
.feature-card h3{margin:0 0 8px;font-size:16px}
.feature-card p{margin:0;color:var(--muted);font-size:14px;line-height:1.6}
.showcase{padding:40px 32px 100px;max-width:1100px;margin:0 auto;width:100%}
.showcase-inner{
  background:var(--surface);border:1px solid var(--border);border-radius:20px;
  padding:40px;box-shadow:var(--shadow-lg);
  display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:center;
}
.showcase h2{font-size:28px;margin:0 0 16px;font-weight:700}
.showcase-list{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:14px}
.showcase-list li{display:flex;align-items:flex-start;gap:12px;font-size:14px;color:var(--muted)}
.showcase-list li .icon{color:var(--success);flex-shrink:0;margin-top:2px}
.showcase-visual{
  height:260px;border-radius:14px;
  background:linear-gradient(135deg,rgba(99,102,241,.12),rgba(139,92,246,.12));
  display:grid;place-items:center;color:var(--primary);
}
.landing-footer{
  padding:32px;border-top:1px solid var(--border);text-align:center;
  color:var(--muted);font-size:13px;margin-top:auto;
}
/* ---------- responsive ---------- */
@media (max-width:900px){
  .sidebar{transform:translateX(-100%)}
  .sidebar.open{transform:translateX(0)}
  .topbar{left:0}
  .hamburger{display:inline-flex}
  .main{margin-left:0;padding:84px 16px 32px}
  .file-grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .showcase-inner{grid-template-columns:1fr;padding:28px;gap:24px}
}
@media (max-width:600px){
  .landing-nav{padding:16px 20px}
  .hero{padding:60px 20px 50px}
  .features,.showcase{padding-left:20px;padding-right:20px}
}
/* ---------- misc ---------- */
code{background:var(--bg-alt);padding:2px 6px;border-radius:5px;font-size:12px;font-family:ui-monospace,monospace}
.hint{color:var(--muted);font-size:12.5px}
hr{border:0;border-top:1px solid var(--border);margin:16px 0}
.flex{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:900px){.grid-2{grid-template-columns:1fr}}
.spin{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div id="toasts" class="toasts"></div>
<script>window.__csrf = "{{ csrf_token() }}";</script>

{% if g.user %}
<aside class="sidebar" id="sidebar">
  <div class="brand">
    <div class="brand-logo">D</div>
    <div class="brand-text">DRZ<span>File Manager</span></div>
  </div>
  <nav class="nav">
    <a href="{{ url_for('index') }}" class="nav-item {% if request.endpoint=='index' %}active{% endif %}"><span class="nav-icon">{{ icon('files', 18) }}</span>Files</a>
    <a href="{{ url_for('trash_view') }}" class="nav-item {% if request.endpoint=='trash_view' %}active{% endif %}"><span class="nav-icon">{{ icon('trash', 18) }}</span>Trash</a>
    <a href="{{ url_for('account') }}" class="nav-item {% if request.endpoint=='account' %}active{% endif %}"><span class="nav-icon">{{ icon('user', 18) }}</span>Account</a>
    {% if g.user['is_admin'] %}
      <a href="{{ url_for('admin_home') }}" class="nav-item {% if request.endpoint in ['admin_home','admin_logs','admin_monitor','admin_change_pin','admin_pin'] %}active{% endif %}"><span class="nav-icon">{{ icon('shield', 18) }}</span>Admin</a>
    {% endif %}
  </nav>
  <div class="sidebar-footer">
    <button class="icon-btn theme-btn" onclick="toggleTheme()" title="Toggle theme" aria-label="Toggle theme">
      <span class="icon-moon">{{ icon('moon', 18) }}</span>
      <span class="icon-sun">{{ icon('sun', 18) }}</span>
    </button>
    <form method="post" action="{{ url_for('logout') }}" style="margin:0">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
      <button class="icon-btn" title="Logout" type="submit" aria-label="Logout">{{ icon('logout', 18) }}</button>
    </form>
  </div>
</aside>
<div class="topbar">
  <button class="hamburger" onclick="document.getElementById('sidebar').classList.toggle('open')" aria-label="Menu">{{ icon('menu', 22) }}</button>
  <div class="spacer"></div>
  <div class="user-chip">
    <span class="user-avatar">{{ g.user['username'][0]|upper }}</span>
    <span>{{ g.user['username'] }}</span>
    {% if g.user['is_admin'] %}<span class="badge badge-admin">admin</span>{% endif %}
  </div>
</div>
{% endif %}

<main class="main {% if not g.user %}{% if landing %}main-landing{% else %}main-auth{% endif %}{% endif %}">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      <div class="flash-stack">
      {% for cat, msg in messages %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}
      </div>
    {% endif %}
  {% endwith %}
  %%BODY%%
</main>

<script>
function toggleTheme(){
  var cur = document.documentElement.getAttribute('data-theme') || 'light';
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try{ localStorage.setItem('drz-theme', next); }catch(e){}
}
document.addEventListener('DOMContentLoaded', function(){
  setTimeout(function(){
    document.querySelectorAll('.flash').forEach(function(el){ el.classList.add('out'); setTimeout(function(){el.remove();},350); });
  }, 5000);
});
function toast(msg, type){
  var el = document.createElement('div');
  el.className = 'toast toast-' + (type||'info');
  el.textContent = msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(function(){ el.classList.add('out'); setTimeout(function(){el.remove();}, 350); }, 3500);
}
function copyText(t){
  navigator.clipboard.writeText(t).then(function(){toast('Copied to clipboard','success');}).catch(function(){toast('Copy failed','error');});
}
function relTime(ts){
  if(!ts) return 'never';
  var d = new Date(String(ts).replace(' ', 'T') + 'Z');
  var s = (Date.now() - d.getTime()) / 1000;
  if(isNaN(s)) return ts;
  if(s < 0) return 'just now';
  if(s < 60) return Math.floor(s) + 's ago';
  if(s < 3600) return Math.floor(s/60) + 'm ago';
  if(s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}
function openPreview(url, name){
  var m = document.getElementById('previewModal');
  if(!m) return;
  document.getElementById('previewImg').src = url;
  document.getElementById('previewName').textContent = name;
  m.classList.add('show');
}
function closePreview(){
  var m = document.getElementById('previewModal'); if(m) m.classList.remove('show');
}
</script>

<div class="modal-backdrop" id="previewModal" onclick="if(event.target===this)closePreview()">
  <div class="modal">
    <button class="modal-close" onclick="closePreview()" aria-label="Close">{{ icon('x', 20) }}</button>
    <h3 id="previewName" style="margin-bottom:14px"></h3>
    <img id="previewImg" alt="preview">
  </div>
</div>
</body>
</html>'''

def render(body, page_title='DRZ File Manager', **ctx):
    ctx['page_title'] = page_title
    return render_template_string(BASE.replace('%%BODY%%', body), **ctx)

CSRF = '<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">'

# ============================================================
# Landing page
# ============================================================
LANDING_BODY = """
<div class="landing">
  <nav class="landing-nav">
    <div class="landing-brand">
      <div class="brand-logo">D</div>
      <div class="brand-text">DRZ<span>File Manager</span></div>
    </div>
    <div class="landing-nav-links">
      <a href="{{ url_for('login') }}"><button>""" + "{{ icon('user', 15) }}" + """ Sign in</button></a>
    </div>
  </nav>

  <section class="hero">
    <h1>Your files, <span class="grad">beautifully organized.</span></h1>
    <p class="hero-sub">A fast, private, and modern file manager with drag &amp; drop uploads, instant previews, shareable links, and powerful admin analytics — all in one place.</p>
    <div class="hero-actions">
      <a href="{{ url_for('login') }}"><button class="btn">""" + "{{ icon('upload', 16) }}" + """ Get started</button></a>
      <a href="#features"><button class="btn btn-secondary">""" + "{{ icon('eye', 16) }}" + """ Explore features</button></a>
    </div>
  </section>

  <section class="features" id="features">
    <h2>Everything you need</h2>
    <div class="features-grid">
      <div class="feature-card">
        <div class="feature-icon">""" + "{{ icon('upload', 26) }}" + """</div>
        <h3>Drag &amp; drop uploads</h3>
        <p>Drop multiple files at once with smooth progress tracking and instant inline previews.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">""" + "{{ icon('link', 26) }}" + """</div>
        <h3>Secure share links</h3>
        <p>Generate time-limited, token-based download links for anyone — no account needed.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">""" + "{{ icon('shield', 26) }}" + """</div>
        <h3>Private &amp; protected</h3>
        <p>CSRF protection, hashed passwords, per-user storage isolation, and a 6-digit admin PIN.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">""" + "{{ icon('activity', 26) }}" + """</div>
        <h3>Real-time analytics</h3>
        <p>Live activity monitor, 7-day charts, file-type breakdowns, and a filterable audit log.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">""" + "{{ icon('trash', 26) }}" + """</div>
        <h3>Safe deletion</h3>
        <p>Deleted files go to trash first — restore or purge permanently with a single click.</p>
      </div>
      <div class="feature-card">
        <div class="feature-icon">""" + "{{ icon('moon', 26) }}" + """</div>
        <h3>Dark mode built-in</h3>
        <p>A polished interface that adapts to your preference — light, dark, and everywhere in between.</p>
      </div>
    </div>
  </section>

  <section class="showcase">
    <div class="showcase-inner">
      <div>
        <h2>Built for everyday use.</h2>
        <ul class="showcase-list">
          <li>""" + "{{ icon('check', 18) }}" + """ <span>Upload gigabytes of files with resumable drag &amp; drop.</span></li>
          <li>""" + "{{ icon('check', 18) }}" + """ <span>Preview images inline — no downloads required.</span></li>
          <li>""" + "{{ icon('check', 18) }}" + """ <span>Organize, search, and sort your files instantly.</span></li>
          <li>""" + "{{ icon('check', 18) }}" + """ <span>Share via expiring links with automatic revocation.</span></li>
          <li>""" + "{{ icon('check', 18) }}" + """ <span>Admin dashboard with user management and audit trails.</span></li>
        </ul>
      </div>
      <div class="showcase-visual">""" + "{{ icon('files', 96) }}" + """</div>
    </div>
  </section>

  <footer class="landing-footer">
    DRZ File Manager — private by design.
  </footer>
</div>
"""

# ============================================================
# Auth
# ============================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        key = f"{client_ip()}|{username.lower()}"
        if _rate_limited(_login_attempts, key):
            audit('login', 'fail', 'rate limited', username)
            flash("Too many failed attempts. Try again in 15 minutes.", "err")
            return render(LOGIN_BODY, page_title='Sign in'), 429
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            _login_attempts.pop(key, None)
            session.clear()
            session['user_id'] = user['id']
            session['last_active'] = time.time()
            g.user = user
            audit('login')
            return redirect(safe_next(request.args.get('next')))
        _record_failure(_login_attempts, key)
        audit('login', 'fail', 'bad credentials', username)
        flash("Invalid username or password.", "err")
    return render(LOGIN_BODY, page_title='Sign in')

LOGIN_BODY = """
<div class="auth-bg">
  <div class="auth-card">
    <div class="auth-brand">
      <div class="brand-logo">D</div>
      <div><h1>DRZ File Manager</h1><span>Secure storage &amp; sharing</span></div>
    </div>
    <h2>Welcome back</h2>
    <p class="sub">Sign in to access your files.</p>
    <form method="post">
      """ + CSRF + """
      <input name="username" placeholder="Username" autofocus required autocomplete="username">
      <input name="password" type="password" placeholder="Password" required autocomplete="current-password">
      <button type="submit">""" + "{{ icon('lock', 15) }}" + """ Sign in</button>
    </form>
    <p class="hint" style="text-align:center;margin-top:18px">Accounts are created by an administrator.</p>
    <p class="hint" style="text-align:center;margin-top:10px"><a href="{{ url_for('index') }}">← Back to home</a></p>
  </div>
</div>
"""

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    audit('logout')
    session.clear()
    return redirect(url_for('login'))

@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    if request.method == 'POST':
        cur, new = request.form.get('current', ''), request.form.get('new', '')
        if not check_password_hash(g.user['password_hash'], cur):
            audit('change_password', 'fail', 'wrong current password')
            flash("Current password is wrong.", "err")
        elif not valid_new_password(new):
            flash("New password must be at least 8 characters.", "err")
        else:
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new), g.user['id']))
            get_db().commit()
            audit('change_password')
            flash("Password changed.", "ok")
    db = get_db()
    files = db.execute("SELECT * FROM files WHERE user_id=? ORDER BY upload_date DESC", (g.user['id'],)).fetchall()
    used = sum(f['file_size'] for f in files)
    shares = db.execute("SELECT COUNT(*) c FROM share_links WHERE created_by=?", (g.user['id'],)).fetchone()['c']
    trash_n = db.execute("SELECT COUNT(*) c FROM trash WHERE user_id=?", (g.user['id'],)).fetchone()['c']
    return render("""
<div class="stats-row">
  <div class="stat-card"><div class="stat-icon">{{ icon('files', 26) }}</div><div class="stat-body"><div class="stat-value">{{ files|length }}</div><div class="stat-label">Files</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('database', 26) }}</div><div class="stat-body"><div class="stat-value">{{ used|filesize }}</div><div class="stat-label">Storage used</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('link', 26) }}</div><div class="stat-body"><div class="stat-value">{{ shares }}</div><div class="stat-label">Active shares</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('trash', 26) }}</div><div class="stat-body"><div class="stat-value">{{ trash_n }}</div><div class="stat-label">In trash</div></div></div>
</div>

<div class="grid-2">
  <div class="card">
    <h3>{{ icon('user', 18) }} Profile</h3>
    <table>
      <tr><td class="hint">Username</td><td><b>{{ g.user['username'] }}</b></td></tr>
      <tr><td class="hint">Role</td><td>{% if g.user['is_admin'] %}<span class="badge badge-admin">administrator</span>{% else %}<span class="badge badge-user">user</span>{% endif %}</td></tr>
      <tr><td class="hint">Member since</td><td>{{ g.user['created_at'] }}</td></tr>
      <tr><td class="hint">Last seen</td><td>{{ g.user['last_seen'] or '—' }}</td></tr>
    </table>
  </div>
  <div class="card">
    <h3>{{ icon('lock', 18) }} Change password</h3>
    <form method="post" style="display:flex;flex-direction:column;gap:10px">
      """ + CSRF + """
      <input type="password" name="current" placeholder="Current password" required>
      <input type="password" name="new" placeholder="New password (8+ chars)" required>
      <button type="submit" style="align-self:flex-start">Update password</button>
    </form>
  </div>
</div>
""", page_title='Account', files=files, used=used, shares=shares, trash_n=trash_n)

# ============================================================
# Files
# ============================================================
def own_file_or_404(file_id):
    f = get_db().execute("SELECT * FROM files WHERE id=? AND user_id=?", (file_id, g.user['id'])).fetchone()
    if not f:
        abort(404)
    return f

@app.route('/')
def index():
    if not g.user:
        return render(LANDING_BODY, page_title='DRZ File Manager', landing=True)
    db = get_db()
    q = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'newest')
    order = {'newest':'upload_date DESC','oldest':'upload_date ASC',
             'name':'original_name ASC','size_desc':'file_size DESC','size_asc':'file_size ASC'
            }.get(sort, 'upload_date DESC')

    if q:
        files = db.execute(f"SELECT * FROM files WHERE user_id=? AND original_name LIKE ? ORDER BY {order}",
                           (g.user['id'], f'%{q}%')).fetchall()
    else:
        files = db.execute(f"SELECT * FROM files WHERE user_id=? ORDER BY {order}",
                           (g.user['id'],)).fetchall()

    links = db.execute("SELECT s.*, f.original_name FROM share_links s JOIN files f ON f.id=s.file_id "
                       "WHERE s.created_by=? ORDER BY s.id DESC", (g.user['id'],)).fetchall()
    used = sum(f['file_size'] for f in db.execute(
        "SELECT file_size FROM files WHERE user_id=?", (g.user['id'],)).fetchall())
    trash_n = db.execute("SELECT COUNT(*) c FROM trash WHERE user_id=?", (g.user['id'],)).fetchone()['c']

    return render("""
<div class="stats-row">
  <div class="stat-card"><div class="stat-icon">{{ icon('files', 26) }}</div><div class="stat-body"><div class="stat-value">{{ files|length }}</div><div class="stat-label">Files</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('database', 26) }}</div><div class="stat-body"><div class="stat-value">{{ used|filesize }}</div><div class="stat-label">Storage used</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('link', 26) }}</div><div class="stat-body"><div class="stat-value">{{ links|length }}</div><div class="stat-label">Share links</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('trash', 26) }}</div><div class="stat-body"><div class="stat-value">{{ trash_n }}</div><div class="stat-label">In trash</div></div></div>
</div>

<div class="card">
  <h3>{{ icon('upload', 18) }} Upload files</h3>
  <div class="drop-zone" id="dropZone">
    <span class="dz-icon">{{ icon('cloud', 48) }}</span>
    <div class="dz-title">Drag &amp; drop files here</div>
    <div class="dz-sub">or <b>click to browse</b> — multiple files supported</div>
    <input type="file" id="fileInput" multiple style="display:none">
  </div>
  <div class="progress-wrap" id="uploadProgressWrap">
    <div class="progress-bar" id="uploadProgress"></div>
  </div>
  <div id="uploadStatus" class="hint" style="margin-top:8px"></div>
</div>

<div class="card">
  <div class="toolbar">
    <h3 style="margin:0">{{ icon('files', 18) }} My files</h3>
    <div class="spacer"></div>
    <form method="get">
      <input type="text" name="q" placeholder="Search files…" value="{{ q }}" style="min-width:200px">
      <select name="sort" onchange="this.form.submit()">
        <option value="newest" {% if sort=='newest' %}selected{% endif %}>Newest first</option>
        <option value="oldest" {% if sort=='oldest' %}selected{% endif %}>Oldest first</option>
        <option value="name" {% if sort=='name' %}selected{% endif %}>Name A–Z</option>
        <option value="size_desc" {% if sort=='size_desc' %}selected{% endif %}>Largest first</option>
        <option value="size_asc" {% if sort=='size_asc' %}selected{% endif %}>Smallest first</option>
      </select>
      <button class="small">{{ icon('filter', 13) }} Filter</button>
    </form>
  </div>

  {% if not files %}
    <p class="hint" style="text-align:center;padding:40px 0">No files yet. Upload some to get started!</p>
  {% else %}
    <div class="file-grid">
    {% for f in files %}
      <div class="file-card">
        {% if is_image(f['original_name']) %}
          <img class="file-thumb" src="{{ url_for('preview', file_id=f['id']) }}" alt="" loading="lazy" onclick="openPreview(this.src, '{{ f['original_name'] }}')" style="cursor:pointer">
        {% else %}
          <div class="file-icon">{{ file_icon(f['original_name'], 40) }}</div>
        {% endif %}
        <div class="file-name" title="{{ f['original_name'] }}">{{ f['original_name'] }}</div>
        <div class="file-meta">{{ f['file_size']|filesize }} • {{ f['upload_date'][:10] }}</div>
        <div class="file-actions">
          <a href="{{ url_for('download', file_id=f['id']) }}"><button class="small">{{ icon('download', 13) }} Download</button></a>
          <form method="post" action="{{ url_for('share', file_id=f['id']) }}">
            """ + CSRF + """
            <input type="hidden" name="hours" value="24">
            <button class="small gray" title="Create 24h share link">{{ icon('share', 13) }} Share</button>
          </form>
          <form method="post" action="{{ url_for('delete_file', file_id=f['id']) }}" onsubmit="return confirm('Move to trash?')">
            """ + CSRF + """
            <button class="small red" title="Move to trash">{{ icon('trash', 13) }}</button>
          </form>
        </div>
      </div>
    {% endfor %}
    </div>
  {% endif %}
</div>

{% if links %}
<div class="card">
  <h3>{{ icon('link', 18) }} Share links</h3>
  <table>
    <tr><th>File</th><th>Link</th><th>Expires (UTC)</th><th></th></tr>
    {% for l in links %}
    <tr>
      <td>{{ file_icon(l['original_name'], 18) }} {{ l['original_name'] }}</td>
      <td><code style="cursor:pointer" onclick="copyText('{{ url_for('shared', token=l['token'], _external=True) }}')">{{ url_for('shared', token=l['token'], _external=True) }}</code></td>
      <td>{{ l['expires_at'] }}</td>
      <td>
        <form class="inline" method="post" action="{{ url_for('revoke_share', link_id=l['id']) }}">
          """ + CSRF + """
          <button class="small gray">Revoke</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endif %}

<script>
(function(){
  var dz = document.getElementById('dropZone');
  var fi = document.getElementById('fileInput');
  if(!dz) return;
  dz.addEventListener('click', function(){ fi.click(); });
  fi.addEventListener('change', function(){ if(fi.files.length) uploadFiles(fi.files); });
  ['dragenter','dragover'].forEach(function(ev){
    dz.addEventListener(ev, function(e){ e.preventDefault(); dz.classList.add('drag'); });
  });
  ['dragleave','drop'].forEach(function(ev){
    dz.addEventListener(ev, function(e){ e.preventDefault(); dz.classList.remove('drag'); });
  });
  dz.addEventListener('drop', function(e){
    if(e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });
  function uploadFiles(files){
    var fd = new FormData();
    for(var i=0;i<files.length;i++) fd.append('files', files[i]);
    fd.append('_csrf_token', window.__csrf);
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '{{ url_for('upload') }}');
    var wrap = document.getElementById('uploadProgressWrap');
    var bar  = document.getElementById('uploadProgress');
    var st   = document.getElementById('uploadStatus');
    wrap.classList.add('show'); bar.style.width = '0%';
    st.textContent = 'Uploading ' + files.length + ' file(s)…';
    xhr.upload.onprogress = function(e){
      if(e.lengthComputable){
        var pct = (e.loaded/e.total)*100;
        bar.style.width = pct + '%';
        st.textContent = 'Uploading… ' + Math.round(pct) + '%';
      }
    };
    xhr.onload = function(){
      if(xhr.status >= 200 && xhr.status < 400){ st.textContent = 'Done! Reloading…'; setTimeout(function(){location.href='{{ url_for('index') }}';}, 400); }
      else { st.textContent = 'Upload failed (' + xhr.status + ').'; }
    };
    xhr.onerror = function(){ st.textContent = 'Network error.'; };
    xhr.send(fd);
  }
})();
</script>
""", page_title='Files', files=files, links=links, used=used, trash_n=trash_n, q=q, sort=sort)

@app.route('/upload', methods=['POST'])
@login_required
def upload():
    folder = get_user_dir(g.user['id'])
    saved = 0
    for f in request.files.getlist('files'):
        orig = secure_filename(f.filename or '')
        if not orig:
            continue
        stored = f"{uuid.uuid4().hex}_{orig}"
        path = os.path.join(folder, stored)
        f.save(path)
        get_db().execute("INSERT INTO files (user_id, filename, original_name, file_size, upload_date) VALUES (?,?,?,?,?)",
                         (g.user['id'], stored, orig, os.path.getsize(path), now_str()))
        saved += 1
    get_db().commit()
    audit('upload', 'ok', f'{saved} file(s)')
    flash(f"Uploaded {saved} file(s)." if saved else "No valid files selected.", "ok" if saved else "warn")
    return redirect(url_for('index'))

@app.route('/download/<int:file_id>')
@login_required
def download(file_id):
    f = own_file_or_404(file_id)
    path = os.path.join(get_user_dir(g.user['id']), f['filename'])
    if not os.path.isfile(path):
        abort(404)
    audit('download', 'ok', f['original_name'])
    return send_file(path, as_attachment=True, download_name=f['original_name'])

@app.route('/preview/<int:file_id>')
@login_required
def preview(file_id):
    f = own_file_or_404(file_id)
    if not is_image(f['original_name']):
        abort(404)
    path = os.path.join(get_user_dir(g.user['id']), f['filename'])
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)

@app.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    f = own_file_or_404(file_id)
    src = os.path.join(get_user_dir(g.user['id']), f['filename'])
    dst = os.path.join(get_trash_dir(g.user['id']), f['filename'])
    if os.path.isfile(src):
        shutil.move(src, dst)
    db = get_db()
    db.execute("INSERT INTO trash (user_id, original_path, deleted_by, original_name, file_size, deleted_at) VALUES (?,?,?,?,?,?)",
               (g.user['id'], f['filename'], g.user['id'], f['original_name'], f['file_size'], now_str()))
    db.execute("DELETE FROM files WHERE id=?", (file_id,))
    db.commit()
    audit('trash', 'ok', f['original_name'])
    flash("Moved to trash.", "ok")
    return redirect(url_for('index'))

@app.route('/trash')
@login_required
def trash_view():
    items = get_db().execute("SELECT * FROM trash WHERE user_id=? ORDER BY deleted_at DESC", (g.user['id'],)).fetchall()
    return render("""
<div class="card">
  <h3>{{ icon('trash', 18) }} Trash</h3>
  {% if not items %}
    <p class="hint" style="text-align:center;padding:32px 0">Trash is empty.</p>
  {% else %}
  <table>
    <tr><th></th><th>Name</th><th>Size</th><th>Deleted (UTC)</th><th></th></tr>
    {% for t in items %}
    <tr>
      <td>{{ file_icon(t['original_name'], 22) }}</td>
      <td>{{ t['original_name'] }}</td>
      <td>{{ t['file_size']|filesize }}</td>
      <td>{{ t['deleted_at'] }}</td>
      <td style="white-space:nowrap">
        <form class="inline" method="post" action="{{ url_for('restore', trash_id=t['id']) }}">
          """ + CSRF + """
          <button class="small">{{ icon('restore', 13) }} Restore</button>
        </form>
        <form class="inline" method="post" action="{{ url_for('purge', trash_id=t['id']) }}" onsubmit="return confirm('Delete permanently? This cannot be undone.')">
          """ + CSRF + """
          <button class="small red">{{ icon('trash', 13) }} Delete forever</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
</div>
""", page_title='Trash', items=items)

def own_trash_or_404(tid):
    t = get_db().execute("SELECT * FROM trash WHERE id=? AND user_id=?", (tid, g.user['id'])).fetchone()
    if not t:
        abort(404)
    return t

@app.route('/trash/restore/<int:trash_id>', methods=['POST'])
@login_required
def restore(trash_id):
    t = own_trash_or_404(trash_id)
    src = os.path.join(get_trash_dir(g.user['id']), t['original_path'])
    dst = os.path.join(get_user_dir(g.user['id']), t['original_path'])
    if not os.path.isfile(src):
        flash("File data is missing; removing the entry.", "err")
    else:
        shutil.move(src, dst)
        get_db().execute("INSERT INTO files (user_id, filename, original_name, file_size, upload_date) VALUES (?,?,?,?,?)",
                         (g.user['id'], t['original_path'], t['original_name'], t['file_size'], now_str()))
        audit('restore', 'ok', t['original_name'])
        flash("Restored.", "ok")
    get_db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
    get_db().commit()
    return redirect(url_for('trash_view'))

@app.route('/trash/purge/<int:trash_id>', methods=['POST'])
@login_required
def purge(trash_id):
    t = own_trash_or_404(trash_id)
    p = os.path.join(get_trash_dir(g.user['id']), t['original_path'])
    if os.path.isfile(p):
        os.remove(p)
    get_db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
    get_db().commit()
    audit('purge', 'ok', t['original_name'])
    flash("Deleted permanently.", "ok")
    return redirect(url_for('trash_view'))

# ============================================================
# Sharing
# ============================================================
@app.route('/share/<int:file_id>', methods=['POST'])
@login_required
def share(file_id):
    f = own_file_or_404(file_id)
    try:
        hours = max(1, min(int(request.form.get('hours', 24)), 24 * 30))
    except ValueError:
        hours = 24
    token = secrets.token_urlsafe(24)
    exp = now_str(datetime.now(timezone.utc) + timedelta(hours=hours))
    get_db().execute("INSERT INTO share_links (token, file_id, created_by, expires_at, created_at) VALUES (?,?,?,?,?)",
                     (token, file_id, g.user['id'], exp, now_str()))
    get_db().commit()
    audit('share_create', 'ok', f['original_name'])
    flash("Share link created (see below).", "ok")
    return redirect(url_for('index'))

@app.route('/share/revoke/<int:link_id>', methods=['POST'])
@login_required
def revoke_share(link_id):
    get_db().execute("DELETE FROM share_links WHERE id=? AND created_by=?", (link_id, g.user['id']))
    get_db().commit()
    audit('share_revoke')
    return redirect(url_for('index'))

@app.route('/s/<token>')
def shared(token):
    row = get_db().execute("SELECT s.*, f.filename, f.original_name, f.user_id FROM share_links s "
                           "JOIN files f ON f.id=s.file_id WHERE s.token=?", (token,)).fetchone()
    if not row or (row['expires_at'] and row['expires_at'] < now_str()):
        audit('share_download', 'fail', 'invalid/expired token', 'anonymous')
        abort(404)
    path = os.path.join(get_user_dir(row['user_id']), row['filename'])
    if not os.path.isfile(path):
        abort(404)
    audit('share_download', 'ok', row['original_name'], 'anonymous')
    return send_file(path, as_attachment=True, download_name=row['original_name'])

# ============================================================
# Admin — PIN
# ============================================================
@app.route('/admin/pin', methods=['GET', 'POST'])
@admin_required
def admin_pin():
    stored = get_setting('admin_pin_hash')
    setup = not stored
    key = f"pin|{g.user['id']}"
    if request.method == 'POST':
        pin = request.form.get('pin', '')
        if _rate_limited(_pin_attempts, key):
            audit('admin_pin', 'fail', 'rate limited')
            flash("Too many wrong PINs. Try again in 15 minutes.", "err")
            return render(PIN_BODY, page_title='Admin PIN', setup=setup), 429
        if not (pin.isascii() and pin.isdigit() and len(pin) == 6):
            flash("PIN must be exactly 6 digits.", "err")
        elif setup:
            if pin != request.form.get('pin2', ''):
                flash("PINs do not match.", "err")
            else:
                set_setting('admin_pin_hash', generate_password_hash(pin))
                set_setting('pin_history', json.dumps([generate_password_hash(pin)]))
                session['pin_ok_at'] = time.time()
                audit('admin_pin_setup')
                return redirect(safe_next(request.args.get('next') or url_for('admin_home')))
        elif check_password_hash(stored, pin):
            _pin_attempts.pop(key, None)
            session['pin_ok_at'] = time.time()
            audit('admin_pin_unlock')
            return redirect(safe_next(request.args.get('next') or url_for('admin_home')))
        else:
            _record_failure(_pin_attempts, key)
            audit('admin_pin', 'fail', 'wrong pin')
            flash("Wrong PIN.", "err")
    return render(PIN_BODY, page_title='Admin PIN', setup=setup)

PIN_BODY = """
<div class="auth-bg">
  <div class="auth-card" style="max-width:380px">
    <h2>{{ 'Create admin PIN' if setup else 'Enter admin PIN' }}</h2>
    <p class="sub">{{ 'Set up a 6-digit PIN to protect the admin panel.' if setup else 'Unlock the admin panel for 10 minutes.' }}</p>
    <form method="post">
      """ + CSRF + """
      <input name="pin" type="password" inputmode="numeric" maxlength="6" pattern="\\d{6}" placeholder="6-digit PIN" required autofocus>
      {% if setup %}<input name="pin2" type="password" inputmode="numeric" maxlength="6" pattern="\\d{6}" placeholder="Repeat PIN" required>{% endif %}
      <button type="submit">{{ icon('lock', 15) }} {{ 'Save PIN' if setup else 'Unlock' }}</button>
    </form>
  </div>
</div>
"""

# ============================================================
# Admin dashboard
# ============================================================
@app.route('/admin')
@pin_required
def admin_home():
    db = get_db()
    users = db.execute("SELECT u.*, (SELECT COUNT(*) FROM files f WHERE f.user_id=u.id) AS nfiles, "
                       "(SELECT COALESCE(SUM(file_size),0) FROM files f WHERE f.user_id=u.id) AS bytes "
                       "FROM users u ORDER BY u.id").fetchall()
    total_users  = db.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
    cutoff       = now_str(datetime.now(timezone.utc) - timedelta(seconds=ONLINE_WINDOW))
    online_n     = db.execute("SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (cutoff,)).fetchone()['c']
    total_files  = db.execute("SELECT COUNT(*) c FROM files").fetchone()['c']
    total_bytes  = db.execute("SELECT COALESCE(SUM(file_size),0) s FROM files").fetchone()['s']
    total_shares = db.execute("SELECT COUNT(*) c FROM share_links").fetchone()['c']

    activity = []
    for i in range(6, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=i)).strftime('%Y-%m-%d')
        c = db.execute("SELECT COUNT(*) c FROM audit_logs WHERE timestamp LIKE ? AND status='ok'", (day + '%',)).fetchone()['c']
        activity.append({'day': day, 'count': c})
    max_activity = max((a['count'] for a in activity), default=0) or 1

    rows = db.execute("SELECT original_name FROM files").fetchall()
    ext_count = {}
    for r in rows:
        n = r['original_name'] or ''
        ext = n.rsplit('.', 1)[-1].lower() if '.' in n else 'other'
        ext_count[ext] = ext_count.get(ext, 0) + 1
    total_ext = sum(ext_count.values()) or 1
    palette = ['#6366f1','#8b5cf6','#ec4899','#f59e0b','#10b981','#0ea5e9','#ef4444','#14b8a6','#f97316','#a855f7']
    top_ext = sorted(ext_count.items(), key=lambda x: -x[1])[:6]
    donut_segments = []
    acc = 0.0
    for i, (ext, cnt) in enumerate(top_ext):
        pct = cnt / total_ext * 100
        donut_segments.append({'ext': ext, 'count': cnt, 'pct': pct, 'color': palette[i % len(palette)]})
        acc += pct
    other_pct = max(0.0, 100.0 - acc)
    if other_pct > 0.01:
        donut_segments.append({'ext': 'other', 'count': max(0, total_ext - sum(x[1] for x in top_ext)), 'pct': other_pct, 'color': '#94a3b8'})

    recent = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 12").fetchall()

    return render("""
<div class="stats-row">
  <div class="stat-card"><div class="stat-icon">{{ icon('users', 26) }}</div><div class="stat-body"><div class="stat-value">{{ total_users }}</div><div class="stat-label">Users</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('activity', 26) }}</div><div class="stat-body"><div class="stat-value">{{ online_n }}</div><div class="stat-label">Online now</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('files', 26) }}</div><div class="stat-body"><div class="stat-value">{{ total_files }}</div><div class="stat-label">Files</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('database', 26) }}</div><div class="stat-body"><div class="stat-value">{{ total_bytes|filesize }}</div><div class="stat-label">Storage</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('link', 26) }}</div><div class="stat-body"><div class="stat-value">{{ total_shares }}</div><div class="stat-label">Share links</div></div></div>
</div>

<div class="grid-2">
  <div class="card">
    <h3>{{ icon('activity', 18) }} Activity (last 7 days)</h3>
    <div class="chart-bars">
      {% for a in activity %}
      <div class="bar-wrap">
        <div class="bar" data-count="{{ a.count }}" style="height: {{ (a.count / max_activity * 100)|round(1) }}%"></div>
        <div class="bar-label">{{ a.day[5:] }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
  <div class="card">
    <h3>{{ icon('pie', 18) }} File types</h3>
    {% if donut_segments %}
      <div class="donut-wrap">
        <svg class="donut" viewBox="0 0 42 42">
          <circle cx="21" cy="21" r="15.915" fill="transparent" stroke="var(--bg-alt)" stroke-width="6"/>
          {% set ns = namespace(offset=25) %}
          {% for seg in donut_segments %}
            <circle cx="21" cy="21" r="15.915" fill="transparent"
                    stroke="{{ seg.color }}" stroke-width="6"
                    stroke-dasharray="{{ seg.pct|round(2) }} {{ 100 - seg.pct|round(2) }}"
                    stroke-dashoffset="{{ ns.offset }}"/>
            {% set ns.offset = ns.offset - seg.pct|round(2) %}
          {% endfor %}
        </svg>
        <div class="legend">
          {% for seg in donut_segments %}
          <div class="legend-item">
            <span class="legend-dot" style="background:{{ seg.color }}"></span>
            <span><b>{{ seg.ext }}</b> — {{ seg.count }} ({{ seg.pct|round(1) }}%)</span>
          </div>
          {% endfor %}
        </div>
      </div>
    {% else %}
      <p class="hint">No files uploaded yet.</p>
    {% endif %}
  </div>
</div>

<div class="card">
  <h3>{{ icon('plus', 18) }} Create user</h3>
  <form method="post" action="{{ url_for('admin_create_user') }}" class="flex">
    """ + CSRF + """
    <input name="username" placeholder="Username" required>
    <input name="password" type="password" placeholder="Password (8+ chars)" required>
    <label style="display:flex;align-items:center;gap:6px;font-size:13px"><input type="checkbox" name="is_admin"> admin</label>
    <button type="submit">{{ icon('plus', 14) }} Create user</button>
  </form>
</div>

<div class="card">
  <h3>{{ icon('users', 18) }} All users</h3>
  <table>
    <tr><th>ID</th><th>User</th><th>Role</th><th>Files</th><th>Storage</th><th>Last seen</th><th></th></tr>
    {% for u in users %}
    <tr>
      <td>{{ u['id'] }}</td>
      <td><b>{{ u['username'] }}</b></td>
      <td>{% if u['is_admin'] %}<span class="badge badge-admin">admin</span>{% else %}<span class="badge badge-user">user</span>{% endif %}</td>
      <td>{{ u['nfiles'] }}</td>
      <td>{{ u['bytes']|filesize }}</td>
      <td class="hint">{{ u['last_seen'] or '—' }}</td>
      <td style="white-space:nowrap">
        <form class="inline" method="post" action="{{ url_for('admin_reset_password', user_id=u['id']) }}">
          """ + CSRF + """
          <input name="password" type="password" placeholder="New password" required style="width:130px">
          <button class="small gray">Reset</button>
        </form>
        {% if u['id'] != g.user['id'] and u['username'] != 'admin' %}
        <form class="inline" method="post" action="{{ url_for('admin_delete_user', user_id=u['id']) }}" onsubmit="return confirm('Delete {{ u['username'] }} and ALL their files?')">
          """ + CSRF + """
          <button class="small red">{{ icon('trash', 13) }}</button>
        </form>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>

<div class="card">
  <h3>{{ icon('bell', 18) }} Recent activity</h3>
  <table>
    <tr><th>Time</th><th>User</th><th>Action</th><th>Status</th><th>Detail</th></tr>
    {% for r in recent %}
    <tr>
      <td class="hint">{{ r['timestamp'] }}</td>
      <td>{{ r['username'] }}</td>
      <td><code>{{ r['action'] }}</code></td>
      <td>{% if r['status']=='ok' %}<span class="badge badge-ok">ok</span>{% else %}<span class="badge badge-fail">{{ r['status'] }}</span>{% endif %}</td>
      <td class="hint">{{ r['failure_reason'] }}</td>
    </tr>
    {% endfor %}
  </table>
  <div class="flex" style="margin-top:12px">
    <a href="{{ url_for('admin_logs') }}"><button class="gray small">{{ icon('list', 13) }} View full audit log</button></a>
    <a href="{{ url_for('admin_monitor') }}"><button class="small">{{ icon('satellite', 13) }} Live monitor</button></a>
    <a href="{{ url_for('admin_change_pin') }}"><button class="gray small">{{ icon('lock', 13) }} Change PIN</button></a>
  </div>
</div>
""", page_title='Admin dashboard', users=users, total_users=total_users, online_n=online_n,
    total_files=total_files, total_bytes=total_bytes, total_shares=total_shares,
    activity=activity, max_activity=max_activity, donut_segments=donut_segments, recent=recent)

@app.route('/admin/users/create', methods=['POST'])
@pin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    pw = request.form.get('password', '')
    if not username or len(username) > 40 or not username.replace('_', '').replace('-', '').replace('.', '').isalnum():
        flash("Username: letters, digits, . _ - only (max 40).", "err")
    elif not valid_new_password(pw):
        flash("Password must be at least 8 characters.", "err")
    else:
        try:
            get_db().execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,?)",
                             (username, generate_password_hash(pw), 1 if request.form.get('is_admin') else 0))
            get_db().commit()
            audit('admin_create_user', 'ok', username)
            flash(f"User '{username}' created.", "ok")
        except sqlite3.IntegrityError:
            flash("That username already exists.", "err")
    return redirect(url_for('admin_home'))

@app.route('/admin/users/<int:user_id>/reset', methods=['POST'])
@pin_required
def admin_reset_password(user_id):
    pw = request.form.get('password', '')
    if not valid_new_password(pw):
        flash("Password must be at least 8 characters.", "err")
    else:
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), user_id))
        get_db().commit()
        audit('admin_reset_password', 'ok', f'user_id={user_id}')
        flash("Password reset.", "ok")
    return redirect(url_for('admin_home'))

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@pin_required
def admin_delete_user(user_id):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u or u['id'] == g.user['id'] or u['username'] == 'admin':
        flash("That user cannot be deleted.", "err")
        return redirect(url_for('admin_home'))
    db.execute("DELETE FROM share_links WHERE created_by=?", (user_id,))
    db.execute("DELETE FROM trash WHERE user_id=? OR deleted_by=?", (user_id, user_id))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    shutil.rmtree(get_user_dir(user_id), ignore_errors=True)
    audit('admin_delete_user', 'ok', u['username'])
    flash(f"User '{u['username']}' deleted.", "ok")
    return redirect(url_for('admin_home'))

@app.route('/admin/pin/change', methods=['GET', 'POST'])
@pin_required
def admin_change_pin():
    if request.method == 'POST':
        old, new = request.form.get('old', ''), request.form.get('new', '')
        if not check_password_hash(get_setting('admin_pin_hash'), old):
            audit('admin_pin_change', 'fail', 'wrong old pin')
            flash("Current PIN is wrong.", "err")
        elif not (new.isascii() and new.isdigit() and len(new) == 6):
            flash("New PIN must be exactly 6 digits.", "err")
        else:
            hist = json.loads(get_setting('pin_history') or '[]')
            if any(check_password_hash(h, new) for h in hist[-5:]):
                flash("You cannot reuse one of your last 5 PINs.", "err")
            else:
                h = generate_password_hash(new)
                hist.append(h)
                set_setting('admin_pin_hash', h)
                set_setting('pin_history', json.dumps(hist[-5:]))
                audit('admin_pin_change')
                flash("PIN changed.", "ok")
                return redirect(url_for('admin_home'))
    return render("""
<div class="card" style="max-width:420px">
  <h3>{{ icon('lock', 18) }} Change admin PIN</h3>
  <form method="post" style="display:flex;flex-direction:column;gap:10px">
    """ + CSRF + """
    <input name="old" type="password" inputmode="numeric" maxlength="6" pattern="\\d{6}" placeholder="Current 6-digit PIN" required>
    <input name="new" type="password" inputmode="numeric" maxlength="6" pattern="\\d{6}" placeholder="New 6-digit PIN" required>
    <button type="submit" style="align-self:flex-start">Change PIN</button>
  </form>
</div>
""", page_title='Change PIN')

# ============================================================
# Audit log (filterable)
# ============================================================
@app.route('/admin/logs')
@pin_required
def admin_logs():
    q      = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    action = request.args.get('action', '')
    limit  = 300

    sql = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    if q:
        sql += " AND (username LIKE ? OR ip_address LIKE ? OR action LIKE ? OR failure_reason LIKE ?)"
        params += [f'%{q}%'] * 4
    if status in ('ok', 'fail'):
        sql += " AND status = ?"
        params.append(status)
    if action:
        sql += " AND action = ?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = get_db().execute(sql, params).fetchall()
    actions = [r['action'] for r in get_db().execute(
        "SELECT DISTINCT action FROM audit_logs WHERE action IS NOT NULL ORDER BY action").fetchall()]
    total = get_db().execute("SELECT COUNT(*) c FROM audit_logs").fetchone()['c']
    fails = get_db().execute("SELECT COUNT(*) c FROM audit_logs WHERE status='fail'").fetchone()['c']

    return render("""
<div class="stats-row">
  <div class="stat-card"><div class="stat-icon">{{ icon('list', 26) }}</div><div class="stat-body"><div class="stat-value">{{ total }}</div><div class="stat-label">Total events</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('alert', 26) }}</div><div class="stat-body"><div class="stat-value">{{ fails }}</div><div class="stat-label">Failed events</div></div></div>
  <div class="stat-card"><div class="stat-icon">{{ icon('search', 26) }}</div><div class="stat-body"><div class="stat-value">{{ rows|length }}</div><div class="stat-label">Showing</div></div></div>
</div>

<div class="card">
  <div class="toolbar">
    <h3 style="margin:0">{{ icon('list', 18) }} Audit log</h3>
    <div class="spacer"></div>
    <form method="get" class="flex">
      <input type="text" name="q" placeholder="Search…" value="{{ q }}">
      <select name="status">
        <option value="">All statuses</option>
        <option value="ok" {% if status=='ok' %}selected{% endif %}>OK</option>
        <option value="fail" {% if status=='fail' %}selected{% endif %}>Failed</option>
      </select>
      <select name="action">
        <option value="">All actions</option>
        {% for a in actions %}<option value="{{ a }}" {% if action==a %}selected{% endif %}>{{ a }}</option>{% endfor %}
      </select>
      <button class="small">{{ icon('filter', 13) }} Apply</button>
      <a href="{{ url_for('admin_logs') }}"><button class="small gray" type="button">Reset</button></a>
    </form>
  </div>
  <table>
    <tr><th>Time (UTC)</th><th>User</th><th>IP</th><th>Action</th><th>Status</th><th>Detail</th><th>User-Agent</th></tr>
    {% for r in rows %}
    <tr>
      <td class="hint" style="white-space:nowrap">{{ r['timestamp'] }}</td>
      <td>{{ r['username'] }}</td>
      <td class="hint">{{ r['ip_address'] }}</td>
      <td><code>{{ r['action'] }}</code></td>
      <td>{% if r['status']=='ok' %}<span class="badge badge-ok">ok</span>{% else %}<span class="badge badge-fail">{{ r['status'] }}</span>{% endif %}</td>
      <td class="hint">{{ r['failure_reason'] }}</td>
      <td class="hint" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ r['user_agent'] }}</td>
    </tr>
    {% endfor %}
  </table>
</div>
""", page_title='Audit log', rows=rows, actions=actions, total=total, fails=fails,
    q=q, status=status, action=action)

# ============================================================
# Live monitor
# ============================================================
@app.route('/admin/monitor')
@pin_required
def admin_monitor():
    return render("""
<div class="card">
  <div class="toolbar">
    <h3 style="margin:0">{{ icon('satellite', 18) }} Eye in the Sky <span class="hint" style="font-weight:400;font-size:12px">— auto-refreshes every 5s</span></h3>
    <div class="spacer"></div>
    <label class="flex" style="font-size:13px;gap:6px;align-items:center">
      <input type="checkbox" id="pauseToggle"> Pause
    </label>
    <input type="text" id="filterInput" placeholder="Filter activity…" style="width:220px">
  </div>
  <div id="users" style="display:flex;flex-direction:column;gap:8px"></div>
</div>

<div class="card">
  <h3>{{ icon('activity', 18) }} Recent activity</h3>
  <table>
    <thead><tr><th>Time</th><th>User</th><th>IP</th><th>Action</th><th>Status</th><th>Detail</th></tr></thead>
    <tbody id="log"></tbody>
  </table>
</div>

<script>
(function(){
  var paused = false;
  var filterText = '';
  var userBox = document.getElementById('users');
  var logBox  = document.getElementById('log');
  var pauseEl = document.getElementById('pauseToggle');
  var filterEl = document.getElementById('filterInput');

  pauseEl.addEventListener('change', function(){ paused = pauseEl.checked; });
  filterEl.addEventListener('input', function(){ filterText = filterEl.value.toLowerCase(); render(logData); });

  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function cell(tr, text){
    var td = document.createElement('td');
    td.innerHTML = esc(text);
    tr.appendChild(td);
  }

  var logData = [];
  function render(data){
    if(!logData.length && !data) return;
    var src = data || logData;
    logBox.replaceChildren();
    src.forEach(function(x){
      var hay = (x.timestamp + ' ' + x.username + ' ' + x.ip_address + ' ' + x.action + ' ' + x.status + ' ' + (x.failure_reason||'')).toLowerCase();
      if(filterText && hay.indexOf(filterText) === -1) return;
      var tr = document.createElement('tr');
      var tdTime = document.createElement('td');
      tdTime.textContent = relTime(x.timestamp);
      tdTime.title = x.timestamp;
      tdTime.className = 'hint';
      tr.appendChild(tdTime);
      cell(tr, x.username);
      cell(tr, x.ip_address);
      var tdAct = document.createElement('td');
      var code = document.createElement('code'); code.textContent = x.action;
      tdAct.appendChild(code); tr.appendChild(tdAct);
      var tdSt = document.createElement('td');
      var b = document.createElement('span');
      b.className = 'badge ' + (x.status === 'ok' ? 'badge-ok' : 'badge-fail');
      b.textContent = x.status; tdSt.appendChild(b); tr.appendChild(tdSt);
      cell(tr, x.failure_reason || '');
      logBox.appendChild(tr);
    });
  }

  async function tick(){
    if(paused) return;
    try {
      var r = await fetch('{{ url_for("admin_monitor_data") }}', { credentials: 'same-origin' });
      if(r.status !== 200){ location.href = '{{ url_for("admin_pin") }}'; return; }
      var d = await r.json();
      userBox.replaceChildren();
      d.users.forEach(function(x){
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--surface-2);border-radius:9px;transition:all .2s';
        var dot = document.createElement('span');
        dot.className = 'dot' + (x.online ? ' on' : '');
        row.appendChild(dot);
        var name = document.createElement('b'); name.textContent = x.username;
        row.appendChild(name);
        var meta = document.createElement('span');
        meta.className = 'hint';
        meta.style.marginLeft = 'auto';
        meta.textContent = x.online ? 'online' : ('last seen ' + relTime(x.last_seen));
        row.appendChild(meta);
        userBox.appendChild(row);
      });
      logData = d.logs;
      render(logData);
    } catch(e) { /* ignore */ }
  }
  tick();
  setInterval(tick, 5000);
})();
</script>
""", page_title='Live monitor')

@app.route('/admin/monitor/data')
@pin_required
def admin_monitor_data():
    db = get_db()
    cutoff = now_str(datetime.now(timezone.utc) - timedelta(seconds=ONLINE_WINDOW))
    users = [{'username': u['username'], 'last_seen': u['last_seen'],
              'online': bool(u['last_seen'] and u['last_seen'] >= cutoff)}
             for u in db.execute("SELECT username, last_seen FROM users ORDER BY last_seen DESC")]
    logs = [dict(r) for r in db.execute(
        "SELECT timestamp, username, ip_address, action, status, failure_reason FROM audit_logs ORDER BY id DESC LIMIT 50")]
    return jsonify(users=users, logs=logs)

# ============================================================
# Error handlers & security headers
# ============================================================
@app.errorhandler(403)
def e403(e):
    return render("<div class='card'><h3>403 — Forbidden</h3><p class='hint'>You don't have access to this page.</p></div>",
                  page_title='Forbidden'), 403

@app.errorhandler(404)
def e404(e):
    return render("<div class='card'><h3>404 — Not found</h3><p class='hint'>The page or file you requested doesn't exist.</p></div>",
                  page_title='Not found'), 404

@app.after_request
def security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    return resp

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 5000)), debug=False)
# Vercel route dispatcher
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

@app.route("/api/index", defaults={"_path": ""}, methods=_METHODS)
@app.route("/api/index/", defaults={"_path": ""}, methods=_METHODS)
@app.route("/api/index/<path:_path>", methods=_METHODS)
def _vercel_dispatch(_path):
    original_path = "/" + _path if _path else "/"
    request.environ["ORIGINAL_PATH"] = original_path
    request.environ["PATH_INFO"] = original_path
    for key in ("path", "full_path", "url", "base_url", "path_url", "host_url"):
        request.__dict__.pop(key, None)
    if _path == "logout" and request.method == "GET":
        session.clear()
        return redirect("/")
    if not g.user and _path in {"account", "trash", "admin", "admin/logs", "admin/monitor", "admin/pin"}:
        return redirect("/login?next=" + original_path)
    endpoint, values = app.url_map.bind_to_environ(request.environ).match()
    return app.view_functions[endpoint](**values)
