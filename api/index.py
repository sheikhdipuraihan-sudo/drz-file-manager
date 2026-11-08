"""
DRZ File Manager — Ultra Edition
A single-file Flask file manager with folders, previews, sharing, trash,
audit logging, admin analytics, PWA support and a polished responsive UI.

Local run:
    pip install -r requirements.txt
    python api/index.py
"""
from flask import (Flask, render_template_string, request, redirect, url_for,
                   flash, session, send_file, abort, g, jsonify, Response)
import os
import io
import re
import json
import csv
import time
import uuid
import shutil
import zipfile
import secrets
import mimetypes
import sqlite3
from datetime import datetime, timedelta, timezone
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from markupsafe import Markup

# ============================================================
# Configuration
# ============================================================
BASE_DIR = (os.environ.get('DRZ_DATA_DIR')
            or ('/tmp/drz-file-manager' if os.environ.get('VERCEL') else os.path.dirname(os.path.abspath(__file__))))
os.makedirs(BASE_DIR, exist_ok=True)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_UPLOAD_MB', '2048')) * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FORCE_HTTPS_COOKIES', '0') == '1'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)

SESSION_TIMEOUT = 30          # minutes
PIN_VALID_MINUTES = 10
ONLINE_WINDOW = 5 * 60        # seconds
DATABASE = os.path.join(BASE_DIR, 'filemanager.db')

_login_attempts, _pin_attempts = {}, {}
RATE_LIMIT_WINDOW = 15 * 60
RATE_LIMIT_MAX = 5


def _load_secret():
    env = os.environ.get('SECRET_KEY')
    if env:
        return env
    path = os.path.join(BASE_DIR, '.secret_key')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                key = f.read().strip()
            if key:
                return key
        except OSError:
            pass
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError:
        pass
    return key


app.secret_key = _load_secret()


def _rate_limited(store, key):
    now = time.time()
    attempts = [t for t in store.get(key, []) if now - t < RATE_LIMIT_WINDOW]
    store[key] = attempts
    return len(attempts) >= RATE_LIMIT_MAX


def _record_failure(store, key):
    store.setdefault(key, []).append(time.time())
    # keep the in-memory store from growing forever
    if len(store) > 2000:
        cutoff = time.time() - RATE_LIMIT_WINDOW
        for k in list(store):
            store[k] = [t for t in store[k] if t >= cutoff]
            if not store[k]:
                store.pop(k, None)


def now_str(dt=None):
    return (dt or datetime.now(timezone.utc)).strftime('%Y-%m-%d %H:%M:%S')


def utcnow():
    return datetime.now(timezone.utc)


# ============================================================
# SVG icon system
# ============================================================
ICONS = {
    'files': '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    'folder': '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    'folder-plus': '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
    'trash': '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    'trash-2': '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    'user': '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    'users': '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    'shield': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    'moon': '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    'sun': '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    'logout': '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    'menu': '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
    'x': '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    'upload': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    'cloud-upload': '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/><polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/>',
    'download': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    'share': '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>',
    'link': '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    'restore': '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    'lock': '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'unlock': '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
    'key': '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777z"/>',
    'database': '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
    'hard-drive': '<line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
    'bell': '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    'activity': '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    'pie': '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    'plus': '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    'minus': '<line x1="5" y1="12" x2="19" y2="12"/>',
    'satellite': '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/>',
    'list': '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    'grid': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
    'alert': '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    'search': '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    'filter': '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    'check': '<polyline points="20 6 9 17 4 12"/>',
    'check-circle': '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    'globe': '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    'cloud': '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
    'eye': '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    'eye-off': '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
    'refresh': '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
    'zap': '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    'heart': '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    'star': '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    'clock': '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    'home': '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    'chevron-right': '<polyline points="9 18 15 12 9 6"/>',
    'chevron-down': '<polyline points="6 9 12 15 18 9"/>',
    'chevron-left': '<polyline points="15 18 9 12 15 6"/>',
    'arrow-left': '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    'arrow-right': '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    'external-link': '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    'more': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    'move': '<polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/>',
    'copy': '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    'edit': '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
    'info': '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    'command': '<path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 0 0 0-6z"/>',
    'palette': '<circle cx="12" cy="12" r="10"/><circle cx="8" cy="8" r="1"/><circle cx="12" cy="6" r="1"/><circle cx="16" cy="8" r="1"/><circle cx="6" cy="12" r="1"/><circle cx="8" cy="16" r="1"/>',
    'maximize': '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
    'zoom-in': '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>',
    'zoom-out': '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    'settings-2': '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    'type': '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
    'file': '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>',
    'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/>',
    'file-pdf': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="9" y2="17"/><line x1="12" y1="13" x2="15" y2="13"/><line x1="12" y1="17" x2="15" y2="17"/>',
    'image': '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    'video': '<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
    'audio': '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    'spreadsheet': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/><line x1="12" y1="9" x2="12" y2="21"/>',
    'presentation': '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    'archive': '<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
    'code': '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    'json': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M10 12l-2 2 2 2"/><path d="M14 12l2 2-2 2"/>',
    'csv': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M8 13h8M8 17h8"/>',
    'backup': '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/><polyline points="12 7 12 12 15 15"/>',
    'gauge': '<path d="M12 21a9 9 0 1 1 9-9"/><path d="M12 12l4-4"/><circle cx="12" cy="12" r="1"/>',
    'package': '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    'sparkles': '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>',
    'rocket': '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
}

FILE_ICON_MAP = {
    'jpg': 'image', 'jpeg': 'image', 'png': 'image', 'gif': 'image', 'svg': 'image',
    'webp': 'image', 'bmp': 'image', 'ico': 'image', 'avif': 'image', 'heic': 'image',
    'mp4': 'video', 'mov': 'video', 'avi': 'video', 'mkv': 'video', 'webm': 'video', 'm4v': 'video',
    'mp3': 'audio', 'wav': 'audio', 'flac': 'audio', 'ogg': 'audio', 'm4a': 'audio', 'aac': 'audio',
    'pdf': 'file-pdf', 'doc': 'file-text', 'docx': 'file-text', 'txt': 'file-text',
    'md': 'file-text', 'rtf': 'file-text', 'log': 'file-text',
    'xls': 'spreadsheet', 'xlsx': 'spreadsheet', 'csv': 'csv',
    'ppt': 'presentation', 'pptx': 'presentation',
    'zip': 'archive', 'rar': 'archive', '7z': 'archive', 'tar': 'archive', 'gz': 'archive',
    'exe': 'settings', 'sh': 'code', 'bat': 'code', 'py': 'code', 'js': 'code',
    'ts': 'code', 'jsx': 'code', 'tsx': 'code', 'html': 'globe', 'css': 'palette',
    'json': 'json', 'xml': 'json', 'yml': 'json', 'yaml': 'json', 'toml': 'json',
    'ttf': 'type', 'woff': 'type', 'woff2': 'type', 'otf': 'type',
}

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp', 'ico', 'avif'}
VIDEO_EXTS = {'mp4', 'webm', 'mov', 'm4v', 'ogv'}
AUDIO_EXTS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'}
TEXT_EXTS = {'txt', 'md', 'json', 'xml', 'yml', 'yaml', 'toml', 'py', 'js', 'ts', 'jsx',
             'tsx', 'css', 'html', 'htm', 'sh', 'bat', 'ps1', 'sql', 'ini', 'cfg', 'log', 'csv'}


def icon(name, size=20, cls=''):
    path = ICONS.get(name, ICONS['file'])
    return Markup(
        f'<svg class="icon {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{path}</svg>'
    )


def ext_of(name):
    return name.rsplit('.', 1)[-1].lower() if name and '.' in name else ''


def file_icon(name, size=20):
    return icon(FILE_ICON_MAP.get(ext_of(name), 'file'), size)


def is_image(name):
    return ext_of(name) in IMAGE_EXTS


def is_video(name):
    return ext_of(name) in VIDEO_EXTS


def is_audio(name):
    return ext_of(name) in AUDIO_EXTS


def is_pdf(name):
    return ext_of(name) == 'pdf'


def is_text(name):
    return ext_of(name) in TEXT_EXTS


def can_preview(name):
    return is_image(name) or is_video(name) or is_audio(name) or is_pdf(name) or is_text(name)


def file_kind(name):
    if is_image(name):
        return 'image'
    if is_video(name):
        return 'video'
    if is_audio(name):
        return 'audio'
    if is_pdf(name):
        return 'pdf'
    if is_text(name):
        return 'text'
    if ext_of(name) in {'zip', 'rar', '7z', 'tar', 'gz'}:
        return 'archive'
    return 'file'


app.jinja_env.globals.update(icon=icon, file_icon=file_icon, is_image=is_image,
                             is_video=is_video, is_audio=is_audio, is_pdf=is_pdf,
                             is_text=is_text, can_preview=can_preview,
                             file_kind=file_kind, ext_of=ext_of)


# ============================================================
# Database
# ============================================================
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def _table_columns(db, table):
    try:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _ensure_column(db, table, column, decl):
    if column not in _table_columns(db, table):
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.Error:
            pass


def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                profile_pic TEXT DEFAULT '',
                quota_bytes INTEGER DEFAULT 0,
                theme TEXT DEFAULT 'system',
                accent TEXT DEFAULT 'indigo',
                last_seen TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                parent_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (parent_id) REFERENCES folders (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                folder_id INTEGER,
                filename TEXT NOT NULL,
                original_name TEXT,
                file_size INTEGER DEFAULT 0,
                mime TEXT DEFAULT '',
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                is_starred INTEGER DEFAULT 0,
                download_count INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS trash (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_path TEXT NOT NULL,
                stored_name TEXT,
                folder_name TEXT,
                mime TEXT DEFAULT '',
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
                password_hash TEXT DEFAULT '',
                max_downloads INTEGER DEFAULT 0,
                download_count INTEGER DEFAULT 0,
                allow_preview INTEGER DEFAULT 1,
                note TEXT DEFAULT '',
                FOREIGN KEY (created_by) REFERENCES users (id),
                FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                username TEXT,
                ip_address TEXT,
                user_agent TEXT,
                action TEXT,
                status TEXT,
                failure_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        """)
        # Backwards-compatible migrations
        for table, cols in {
            'users': [('display_name', "TEXT DEFAULT ''"), ('quota_bytes', 'INTEGER DEFAULT 0'),
                      ('theme', "TEXT DEFAULT 'system'"), ('accent', "TEXT DEFAULT 'indigo'")],
            'files': [('folder_id', 'INTEGER'), ('mime', "TEXT DEFAULT ''"),
                      ('updated_at', 'TIMESTAMP'), ('is_starred', 'INTEGER DEFAULT 0'),
                      ('download_count', 'INTEGER DEFAULT 0')],
            'trash': [('stored_name', 'TEXT'), ('folder_name', 'TEXT'), ('mime', "TEXT DEFAULT ''")],
            'share_links': [('password_hash', "TEXT DEFAULT ''"), ('max_downloads', 'INTEGER DEFAULT 0'),
                            ('download_count', 'INTEGER DEFAULT 0'), ('allow_preview', 'INTEGER DEFAULT 1'),
                            ('note', "TEXT DEFAULT ''")],
            'audit_logs': [('user_id', 'INTEGER')],
        }.items():
            for col, decl in cols:
                _ensure_column(db, table, col, decl)
        defaults = {
            'app_name': 'DRZ File Manager',
            'allow_registration': '0',
            'max_upload_mb': str(int(os.environ.get('MAX_UPLOAD_MB', '2048'))),
            'trash_retention_days': '30',
            'share_default_hours': '24',
            'default_quota_mb': '0',
            'maintenance': '0',
            'footer_note': 'Private by design.',
        }
        for k, v in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
        db.commit()


def create_default_admin():
    with app.app_context():
        db = get_db()
        if not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            db.execute("INSERT INTO users (username, display_name, password_hash, is_admin) VALUES (?,?,?,1)",
                       ('admin', 'Administrator', generate_password_hash('00000000')))
            db.commit()
            print("=" * 60)
            print(" Default admin created -> username: admin  password: 00000000")
            print(" CHANGE THIS PASSWORD after first login.")
            print("=" * 60)


os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
init_db()
create_default_admin()

# ============================================================
# CSRF
# ============================================================
def get_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


app.jinja_env.globals['csrf_token'] = get_csrf_token


@app.before_request
def csrf_protect():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = session.get('_csrf_token')
        sent = request.form.get('_csrf_token', '') or request.headers.get('X-CSRF-Token', '')
        if not token or not sent or not secrets.compare_digest(token.encode(), sent.encode()):
            abort(400, description="Invalid or missing CSRF token.")


# ============================================================
# Helpers
# ============================================================
def get_user_dir(user_id):
    path = os.path.join(app.config['UPLOAD_FOLDER'], f'user_{int(user_id)}')
    os.makedirs(path, exist_ok=True)
    return path


def get_trash_dir(user_id):
    path = os.path.join(get_user_dir(user_id), '.trash')
    os.makedirs(path, exist_ok=True)
    return path


def format_size(num_bytes):
    try:
        n = float(num_bytes or 0)
    except (TypeError, ValueError):
        return '0 B'
    if n < 1024:
        return f"{int(n)} B"
    for unit in ('KB', 'MB', 'GB', 'TB', 'PB'):
        n /= 1024.0
        if n < 1024 or unit == 'PB':
            return f"{n:.1f} {unit}"


app.jinja_env.filters['filesize'] = format_size


def client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.headers.get('X-Real-IP') or request.remote_addr or '-'


def audit(action, status='ok', reason='', username=None):
    try:
        u = username or (g.user['username'] if getattr(g, 'user', None) else '-')
        uid = g.user['id'] if getattr(g, 'user', None) else None
        db = get_db()
        db.execute("INSERT INTO audit_logs (timestamp, user_id, username, ip_address, user_agent, action, status, failure_reason)"
                   " VALUES (?,?,?,?,?,?,?,?)",
                   (now_str(), uid, u, client_ip(), (request.headers.get('User-Agent') or '')[:200],
                    action, status, reason))
        db.commit()
    except Exception:
        pass


def safe_next(url):
    return url if url and url.startswith('/') and not url.startswith('//') else url_for('index')


def valid_new_password(pw):
    return isinstance(pw, str) and len(pw) >= 8


def valid_username(name):
    return bool(name) and len(name) <= 40 and re.fullmatch(r'[A-Za-z0-9_.\-]+', name) is not None


def clean_filename(name):
    name = (name or '').replace('\\', '/').split('/')[-1]
    name = ''.join(ch for ch in name if ch >= ' ' and ch != '\x7f')
    name = name.strip().strip('.')
    if not name:
        name = 'file'
    if len(name) > 200:
        base, dot, ext = name.rpartition('.')
        name = (base[:190] + '.' + ext[:9]) if dot else name[:200]
    return name


def stored_name_for(original):
    ext = ext_of(original)
    ext = re.sub(r'[^a-z0-9]', '', ext)[:10]
    return uuid.uuid4().hex + ('.' + ext if ext else '')


def get_setting(key, default=''):
    try:
        row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row is not None else default
    except Exception:
        return default


def set_setting(key, value):
    db = get_db()
    db.execute("INSERT INTO settings (key,value) VALUES (?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db.commit()


def setting_int(key, default=0):
    try:
        return int(get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


def app_name():
    return get_setting('app_name', 'DRZ File Manager') or 'DRZ File Manager'


def used_bytes(user_id):
    row = get_db().execute("SELECT COALESCE(SUM(file_size),0) s FROM files WHERE user_id=?", (user_id,)).fetchone()
    return int(row['s'] if row else 0)


def quota_bytes(user):
    try:
        q = int(user['quota_bytes'] or 0)
    except (KeyError, TypeError, ValueError):
        q = 0
    if q <= 0:
        q = setting_int('default_quota_mb', 0) * 1024 * 1024
    return q


def quota_pct(user):
    q = quota_bytes(user)
    if not q:
        return 0
    return min(100, round(used_bytes(user['id']) / q * 100, 1))


def get_folder_or_404(folder_id):
    folder = get_db().execute("SELECT * FROM folders WHERE id=? AND user_id=?",
                              (folder_id, g.user['id'])).fetchone()
    if not folder:
        abort(404)
    return folder


def folder_breadcrumbs(folder_id):
    crumbs = []
    seen = set()
    db = get_db()
    while folder_id and folder_id not in seen:
        seen.add(folder_id)
        row = db.execute("SELECT id, name, parent_id FROM folders WHERE id=? AND user_id=?",
                         (folder_id, g.user['id'])).fetchone()
        if not row:
            break
        crumbs.append(row)
        folder_id = row['parent_id']
    return list(reversed(crumbs))


def folder_children(parent_id, user_id):
    if parent_id:
        return get_db().execute("SELECT * FROM folders WHERE user_id=? AND parent_id=? ORDER BY name COLLATE NOCASE",
                                (user_id, parent_id)).fetchall()
    return get_db().execute("SELECT * FROM folders WHERE user_id=? AND parent_id IS NULL ORDER BY name COLLATE NOCASE",
                            (user_id,)).fetchall()


def folder_file_count(folder_id, user_id):
    return get_db().execute("SELECT COUNT(*) c FROM files WHERE user_id=? AND folder_id IS ?",
                            (user_id, folder_id)).fetchone()['c']


def all_folders_flat(user_id):
    rows = get_db().execute("SELECT * FROM folders WHERE user_id=? ORDER BY name COLLATE NOCASE", (user_id,)).fetchall()
    by_parent = {}
    for r in rows:
        by_parent.setdefault(r['parent_id'], []).append(r)
    out = []

    def walk(parent, depth):
        for r in by_parent.get(parent, []):
            out.append({'id': r['id'], 'name': r['name'], 'depth': depth})
            walk(r['id'], depth + 1)

    walk(None, 0)
    return out


def folder_descendants(folder_id, user_id):
    ids = [folder_id]
    idx = 0
    while idx < len(ids):
        rows = get_db().execute("SELECT id FROM folders WHERE user_id=? AND parent_id=?", (user_id, ids[idx])).fetchall()
        ids.extend([r['id'] for r in rows])
        idx += 1
    return ids


def folder_size(folder_id, user_id):
    ids = folder_descendants(folder_id, user_id)
    if not ids:
        return 0
    marks = ','.join('?' * len(ids))
    row = get_db().execute(f"SELECT COALESCE(SUM(file_size),0) s FROM files WHERE user_id=? AND folder_id IN ({marks})",
                           (user_id, *ids)).fetchone()
    return int(row['s'] if row else 0)


def own_file_or_404(file_id):
    f = get_db().execute("SELECT * FROM files WHERE id=? AND user_id=?",
                         (file_id, g.user['id'])).fetchone()
    if not f:
        abort(404)
    return f


def own_trash_or_404(tid):
    t = get_db().execute("SELECT * FROM trash WHERE id=? AND user_id=?",
                         (tid, g.user['id'])).fetchone()
    if not t:
        abort(404)
    return t


def physical_path(file_row):
    return os.path.join(get_user_dir(file_row['user_id']), file_row['filename'])


def trash_physical_path(t):
    name = t['stored_name'] or t['original_path']
    return os.path.join(get_trash_dir(t['user_id']), name)


def unique_trash_name(user_id, name):
    path = os.path.join(get_trash_dir(user_id), name)
    if not os.path.exists(path):
        return name
    base, dot, ext = name.rpartition('.')
    return f"{base}-{uuid.uuid4().hex[:8]}.{ext}" if dot else f"{name}-{uuid.uuid4().hex[:8]}"


def purge_expired_trash():
    days = setting_int('trash_retention_days', 30)
    if days <= 0:
        return
    cutoff = now_str(utcnow() - timedelta(days=days))
    db = get_db()
    rows = db.execute("SELECT * FROM trash WHERE deleted_at < ?", (cutoff,)).fetchall()
    for t in rows:
        try:
            p = trash_physical_path(t)
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    if rows:
        db.execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,))
        db.commit()


def move_file_to_trash(file_row, folder_name=''):
    name = unique_trash_name(file_row['user_id'], file_row['filename'])
    src = physical_path(file_row)
    dst = os.path.join(get_trash_dir(file_row['user_id']), name)
    if os.path.isfile(src):
        shutil.move(src, dst)
    db = get_db()
    db.execute("INSERT INTO trash (user_id, original_path, stored_name, folder_name, mime, deleted_by, "
               "original_name, file_size, deleted_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (file_row['user_id'], file_row['filename'], name, folder_name, file_row['mime'] or '',
                g.user['id'], file_row['original_name'], file_row['file_size'], now_str()))
    db.execute("DELETE FROM files WHERE id=?", (file_row['id'],))
    db.commit()
    return name


def log_action(action, status='ok', reason='', username=None):
    audit(action, status, reason, username)


@app.before_request
def load_user():
    g.user = None
    if request.endpoint in ('static',):
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
            return redirect('/login?next=' + (request.environ.get('ORIGINAL_PATH') or request.path or '/'))
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
            return redirect(url_for('admin_pin', next=(request.environ.get('ORIGINAL_PATH') or request.path or '/')))
        return f(*a, **kw)
    return wrapper


# ============================================================
# Stylesheet
# ============================================================
CSS = r'''
:root{
  --accent:#6366f1; --accent-2:#8b5cf6; --accent-rgb:99,102,241;
  --bg:#f5f7fc; --bg-2:#eef2fa; --surface:#ffffff; --surface-2:#f7f9fd;
  --text:#0f172a; --muted:#64748b; --muted-2:#94a3b8; --border:#e4e9f2;
  --success:#10b981; --danger:#ef4444; --warning:#f59e0b; --info:#0ea5e9;
  --radius:16px; --radius-sm:11px; --radius-xs:8px;
  --shadow:0 1px 2px rgba(15,23,42,.04),0 8px 24px rgba(15,23,42,.06);
  --shadow-lg:0 20px 60px rgba(15,23,42,.16);
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,'Inter',sans-serif;
  --mono:ui-monospace,SFMono-Regular,Consolas,'Liberation Mono',monospace;
  --sidebar:264px;
}
[data-theme="dark"]{
  --bg:#080d19; --bg-2:#0d1424; --surface:#101827; --surface-2:#162033;
  --text:#f1f5f9; --muted:#94a3b8; --muted-2:#64748b; --border:#1e293b;
  --shadow:0 1px 2px rgba(0,0,0,.35),0 12px 32px rgba(0,0,0,.35);
  --shadow-lg:0 24px 80px rgba(0,0,0,.65);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
html,body{margin:0;padding:0}
body{
  font-family:var(--font);background:var(--bg);color:var(--text);min-height:100vh;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
  transition:background .25s ease,color .25s ease;
}
::selection{background:rgba(var(--accent-rgb),.22)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:none}
button{font-family:inherit}
.icon{display:inline-block;vertical-align:-.16em;flex-shrink:0}
.icon-btn{
  width:38px;height:38px;border-radius:11px;border:1px solid var(--border);
  background:var(--surface);color:var(--muted);display:inline-grid;place-items:center;
  cursor:pointer;transition:all .18s ease;
}
.icon-btn:hover{color:var(--accent);border-color:rgba(var(--accent-rgb),.4);background:rgba(var(--accent-rgb),.08);transform:translateY(-1px)}
.icon-btn.small{width:32px;height:32px;border-radius:9px}

/* ---------- buttons ---------- */
.btn,button.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:7px;
  padding:10px 16px;border-radius:10px;border:1px solid transparent;cursor:pointer;
  font-size:13px;font-weight:650;line-height:1;white-space:nowrap;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;
  box-shadow:0 6px 18px rgba(var(--accent-rgb),.28);
  transition:transform .16s ease,box-shadow .16s ease,filter .16s ease;
}
.btn:hover{transform:translateY(-1px);box-shadow:0 10px 26px rgba(var(--accent-rgb),.38);filter:brightness(1.04)}
.btn:active{transform:translateY(0)}
.btn:disabled{opacity:.55;cursor:not-allowed;transform:none;box-shadow:none}
.btn.ghost{background:var(--surface);color:var(--text);border-color:var(--border);box-shadow:none}
.btn.ghost:hover{background:var(--surface-2);border-color:rgba(var(--accent-rgb),.35);color:var(--accent)}
.btn.soft{background:rgba(var(--accent-rgb),.1);color:var(--accent);box-shadow:none;border-color:rgba(var(--accent-rgb),.16)}
.btn.soft:hover{background:rgba(var(--accent-rgb),.16)}
.btn.danger{background:linear-gradient(135deg,#f87171,#dc2626);box-shadow:0 6px 18px rgba(239,68,68,.25)}
.btn.success{background:linear-gradient(135deg,#34d399,#059669);box-shadow:0 6px 18px rgba(16,185,129,.25)}
.btn.sm{padding:7px 11px;font-size:12px;border-radius:9px}
.btn.xs{padding:5px 9px;font-size:11px;border-radius:7px;gap:4px}
.btn.block{width:100%}
.btn-icon{padding:8px;width:34px;height:34px}
button:focus-visible,.btn:focus-visible,.icon-btn:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* ---------- forms ---------- */
input,select,textarea{
  width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:10px;
  background:var(--surface);color:var(--text);font-family:inherit;font-size:13px;
  transition:border-color .18s,box-shadow .18s,background .18s;outline:none;
}
input::placeholder,textarea::placeholder{color:var(--muted-2)}
input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.14)}
input[type="checkbox"]{width:16px;height:16px;accent-color:var(--accent);cursor:pointer}
input[type="color"]{padding:3px;height:38px}
label{font-size:13px;font-weight:600}
.field{display:flex;flex-direction:column;gap:6px}
.field label{color:var(--muted);font-size:12px;font-weight:600;letter-spacing:.01em}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.form-grid .full{grid-column:1/-1}
.form-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.input-group{position:relative}
.input-group .icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted-2)}
.input-group input{padding-left:38px}
.pw-wrap{position:relative}
.pw-wrap input{padding-right:42px}
.pw-toggle{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:transparent;border:0;color:var(--muted);cursor:pointer;padding:6px;border-radius:7px;display:grid;place-items:center}
.pw-toggle:hover{background:var(--surface-2);color:var(--text)}
.hint{color:var(--muted);font-size:12.5px;line-height:1.55}
.mono{font-family:var(--mono);font-size:12px}
code{background:var(--bg-2);padding:2px 6px;border-radius:6px;font-size:12px;font-family:var(--mono)}
kbd{font-family:var(--mono);font-size:10.5px;padding:2px 6px;border-radius:5px;border:1px solid var(--border);background:var(--surface-2);color:var(--muted);box-shadow:0 1px 0 var(--border)}
hr{border:0;border-top:1px solid var(--border);margin:18px 0}
.flex{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.flex.between{justify-content:space-between}
.flex.end{justify-content:flex-end}
.grow{flex:1;min-width:0}
.grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.stack{display:flex;flex-direction:column;gap:14px}

/* ---------- shell ---------- */
.sidebar{
  position:fixed;inset:0 auto 0 0;width:var(--sidebar);z-index:120;
  background:linear-gradient(180deg,var(--surface) 0%,var(--surface-2) 100%);
  border-right:1px solid var(--border);padding:18px 14px;
  display:flex;flex-direction:column;gap:8px;
  transition:transform .28s cubic-bezier(.4,0,.2,1);
}
.brand{display:flex;align-items:center;gap:11px;padding:4px 8px 16px;text-decoration:none;color:var(--text)}
.brand-logo{
  width:40px;height:40px;border-radius:12px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;
  font-weight:800;font-size:18px;box-shadow:0 8px 22px rgba(var(--accent-rgb),.32);
  flex-shrink:0;letter-spacing:-.03em;
}
.brand-text{font-weight:750;font-size:14.5px;line-height:1.15;letter-spacing:-.01em}
.brand-text span{display:block;font-size:10.5px;color:var(--muted);font-weight:550;margin-top:2px;letter-spacing:.02em;text-transform:uppercase}
.sidebar-search{
  display:flex;align-items:center;gap:8px;padding:9px 11px;margin:0 2px 8px;
  border:1px solid var(--border);background:var(--surface);border-radius:10px;
  color:var(--muted);font-size:12.5px;cursor:pointer;transition:all .18s;
}
.sidebar-search:hover{border-color:rgba(var(--accent-rgb),.4);color:var(--text);box-shadow:0 4px 14px rgba(var(--accent-rgb),.08)}
.sidebar-search .grow{text-align:left}
.nav{display:flex;flex-direction:column;gap:3px;flex:1;overflow-y:auto;padding:2px}
.nav-label{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted-2);padding:12px 12px 5px}
.nav-item{
  display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:10px;
  color:var(--muted);font-size:13.5px;font-weight:600;text-decoration:none;
  transition:all .18s;position:relative;
}
.nav-item:hover{background:var(--bg-2);color:var(--text)}
.nav-item.active{background:rgba(var(--accent-rgb),.11);color:var(--accent)}
.nav-item.active::before{
  content:"";position:absolute;left:-14px;top:50%;transform:translateY(-50%);
  width:3px;height:20px;border-radius:0 3px 3px 0;background:var(--accent);
}
.nav-item .nav-icon{width:20px;display:grid;place-items:center}
.nav-count{margin-left:auto;font-size:10.5px;font-weight:700;background:var(--bg-2);color:var(--muted);border-radius:99px;padding:2px 7px}
.nav-item.active .nav-count{background:rgba(var(--accent-rgb),.15);color:var(--accent)}
.sidebar-storage{
  background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:12px;margin:8px 2px;
}
.storage-head{display:flex;align-items:center;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-bottom:8px}
.storage-head b{color:var(--text);font-size:11.5px}
.meter{height:7px;background:var(--bg-2);border-radius:99px;overflow:hidden}
.meter > span{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent-2));transition:width .6s cubic-bezier(.4,0,.2,1)}
.storage-foot{display:flex;justify-content:space-between;font-size:10.5px;color:var(--muted-2);margin-top:7px}
.sidebar-footer{display:flex;align-items:center;gap:8px;padding:10px 2px 0;border-top:1px solid var(--border);margin-top:4px}
.sidebar-user{display:flex;align-items:center;gap:9px;min-width:0;flex:1}
.avatar{
  width:34px;height:34px;border-radius:50%;display:grid;place-items:center;flex-shrink:0;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;
  font-weight:750;font-size:13px;box-shadow:0 4px 12px rgba(var(--accent-rgb),.25);
}
.avatar.lg{width:56px;height:56px;font-size:21px}
.sidebar-user b{display:block;font-size:12.5px;line-height:1.1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sidebar-user span{display:block;font-size:10.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px}

.topbar{
  position:fixed;top:0;left:var(--sidebar);right:0;height:64px;z-index:100;
  display:flex;align-items:center;gap:14px;padding:0 24px;
  background:color-mix(in srgb,var(--bg) 78%,transparent);
  backdrop-filter:saturate(180%) blur(16px);-webkit-backdrop-filter:saturate(180%) blur(16px);
  border-bottom:1px solid var(--border);transition:left .28s;
}
.topbar-title{font-size:15px;font-weight:750;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar-title small{display:block;font-size:11px;color:var(--muted);font-weight:500;margin-top:1px}
.topbar-actions{margin-left:auto;display:flex;align-items:center;gap:8px}
.topbar-search{
  display:flex;align-items:center;gap:8px;padding:8px 10px 8px 12px;border-radius:10px;
  border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;
  font-size:12.5px;min-width:220px;transition:all .18s;
}
.topbar-search:hover{border-color:rgba(var(--accent-rgb),.4);color:var(--text)}
.topbar-search .grow{text-align:left}
.hamburger{display:none}
.dropdown{position:relative}
.dropdown-menu{
  position:absolute;right:0;top:calc(100% + 8px);min-width:200px;z-index:220;
  background:var(--surface);border:1px solid var(--border);border-radius:13px;
  box-shadow:var(--shadow-lg);padding:6px;display:none;animation:popIn .16s ease;
}
.dropdown.open .dropdown-menu{display:block}
.dropdown-menu a,.dropdown-menu button{
  display:flex;align-items:center;gap:9px;width:100%;padding:9px 10px;border-radius:8px;
  background:transparent;border:0;color:var(--text);font-size:12.5px;font-weight:600;
  cursor:pointer;text-align:left;text-decoration:none;
}
.dropdown-menu a:hover,.dropdown-menu button:hover{background:var(--bg-2);color:var(--accent)}
.dropdown-menu .sep{height:1px;background:var(--border);margin:5px 4px}
.accent-dots{display:flex;gap:6px;padding:8px 10px}
.accent-dot{width:20px;height:20px;border-radius:50%;border:2px solid transparent;cursor:pointer;padding:0;box-shadow:inset 0 0 0 2px var(--surface)}
.accent-dot:hover{transform:scale(1.12)}
.accent-dot.active{border-color:var(--text)}

.main{
  margin-left:var(--sidebar);padding:88px 26px 96px;max-width:1440px;
  animation:fadeIn .3s ease;
}
.main.full{margin-left:0;padding:0;max-width:none}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes popIn{from{opacity:0;transform:translateY(-4px) scale(.98)}to{opacity:1;transform:none}}

/* ---------- cards / stats ---------- */
.card{
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:22px;box-shadow:var(--shadow);margin-bottom:18px;
  animation:rise .35s cubic-bezier(.4,0,.2,1) both;
}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.card.flush{padding:0;overflow:hidden}
.card-head{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.card-head h3{margin:0;font-size:15px;font-weight:750;display:flex;align-items:center;gap:9px;letter-spacing:-.01em}
.card-head .spacer{flex:1}
.card-head .hint{margin:0}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:14px;margin-bottom:18px}
.stat{
  background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;
  display:flex;align-items:center;gap:13px;box-shadow:var(--shadow);transition:all .2s;
  animation:rise .35s cubic-bezier(.4,0,.2,1) both;
}
.stat:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:rgba(var(--accent-rgb),.3)}
.stat-ico{width:46px;height:46px;border-radius:12px;display:grid;place-items:center;background:rgba(var(--accent-rgb),.1);color:var(--accent);flex-shrink:0}
.stat-ico.green{background:rgba(16,185,129,.1);color:#059669}
.stat-ico.amber{background:rgba(245,158,11,.12);color:#d97706}
.stat-ico.rose{background:rgba(244,63,94,.1);color:#e11d48}
.stat-ico.sky{background:rgba(14,165,233,.1);color:#0284c7}
.stat-val{font-size:21px;font-weight:800;line-height:1.05;letter-spacing:-.02em}
.stat-label{font-size:11.5px;color:var(--muted);font-weight:600;margin-top:3px}
.section-title{display:flex;align-items:center;gap:10px;margin:26px 0 14px;font-size:16px;font-weight:750;letter-spacing:-.01em}
.section-title .spacer{flex:1}

/* ---------- toolbar / breadcrumbs ---------- */
.toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:14px}
.toolbar .spacer{flex:1}
.toolbar form{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.toolbar input,.toolbar select{width:auto;min-width:150px}
.breadcrumbs{display:flex;align-items:center;gap:5px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);min-width:0}
.breadcrumbs a{color:var(--muted);font-weight:600;padding:4px 7px;border-radius:7px}
.breadcrumbs a:hover{background:var(--bg-2);color:var(--text)}
.breadcrumbs .sep{color:var(--muted-2)}
.breadcrumbs .current{color:var(--text);font-weight:700;padding:4px 7px}
.view-toggle{display:flex;gap:2px;background:var(--bg-2);border:1px solid var(--border);border-radius:9px;padding:3px}
.view-toggle button{background:transparent;border:0;color:var(--muted);width:30px;height:28px;border-radius:6px;cursor:pointer;display:grid;place-items:center}
.view-toggle button.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 4px rgba(0,0,0,.08)}

/* ---------- dropzone ---------- */
.dropzone{
  border:2px dashed var(--border);border-radius:14px;padding:28px 18px;text-align:center;
  cursor:pointer;transition:all .22s;background:var(--surface-2);position:relative;
}
.dropzone:hover,.dropzone.drag{border-color:var(--accent);background:rgba(var(--accent-rgb),.05);transform:scale(1.003)}
.dropzone.drag{box-shadow:0 0 0 4px rgba(var(--accent-rgb),.12)}
.dropzone .dz-ico{width:54px;height:54px;border-radius:15px;background:rgba(var(--accent-rgb),.1);color:var(--accent);display:grid;place-items:center;margin:0 auto 12px}
.dropzone .dz-title{font-weight:750;font-size:14px;margin-bottom:4px}
.dropzone .dz-sub{color:var(--muted);font-size:12px}
.dropzone .dz-sub b{color:var(--accent)}
.upload-list{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.upload-item{display:flex;align-items:center;gap:10px;padding:8px 10px;background:var(--surface-2);border-radius:9px;font-size:12px}
.upload-item .grow{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.upload-item .meter{width:110px;height:6px}
.progress-wrap{height:7px;background:var(--bg-2);border-radius:99px;overflow:hidden;margin-top:12px;display:none}
.progress-wrap.show{display:block}
.progress-bar{height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent-2));border-radius:99px;transition:width .18s}

/* ---------- file grid ---------- */
.folder-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin-bottom:18px}
.folder-card{
  display:flex;align-items:center;gap:11px;padding:13px 14px;background:var(--surface);
  border:1px solid var(--border);border-radius:12px;transition:all .2s;position:relative;min-width:0;
}
.folder-card:hover{transform:translateY(-2px);border-color:rgba(var(--accent-rgb),.35);box-shadow:var(--shadow)}
.folder-ico{width:38px;height:38px;border-radius:10px;display:grid;place-items:center;background:rgba(245,158,11,.12);color:#d97706;flex-shrink:0}
.folder-card .grow{min-width:0}
.folder-card b{display:block;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.folder-card span{display:block;font-size:10.5px;color:var(--muted);margin-top:2px}
.folder-card .folder-actions{margin-left:auto;display:flex;gap:2px;opacity:0;transition:opacity .16s}
.folder-card:hover .folder-actions{opacity:1}
.folder-card .folder-actions button{background:transparent;border:0;color:var(--muted);padding:5px;border-radius:6px;cursor:pointer;display:grid;place-items:center}
.folder-card .folder-actions button:hover{background:var(--bg-2);color:var(--accent)}
.file-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));gap:13px}
.file-card{
  background:var(--surface);border:1px solid var(--border);border-radius:13px;padding:12px;
  transition:all .2s;position:relative;overflow:hidden;min-width:0;
  animation:rise .3s cubic-bezier(.4,0,.2,1) both;
}
.file-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-lg);border-color:rgba(var(--accent-rgb),.35)}
.file-card.selected{border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.14)}
.file-thumb{
  width:100%;height:112px;object-fit:cover;border-radius:9px;margin-bottom:10px;
  background:var(--bg-2);display:block;cursor:pointer;
}
.thumb-fallback{
  width:100%;height:112px;border-radius:9px;margin-bottom:10px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--surface-2),var(--bg-2));color:var(--accent);cursor:pointer;
  position:relative;overflow:hidden;
}
.thumb-fallback::after{content:"";position:absolute;inset:0;background:radial-gradient(circle at 30% 20%,rgba(var(--accent-rgb),.12),transparent 60%)}
.file-name{font-weight:700;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:4px}
.file-meta{color:var(--muted);font-size:10.5px;display:flex;gap:6px;align-items:center;margin-bottom:9px;flex-wrap:wrap}
.file-meta .dot-sep{width:3px;height:3px;border-radius:50%;background:var(--muted-2)}
.file-actions{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.file-actions form{display:inline;margin:0}
.file-actions .btn{box-shadow:none}
.file-check{position:absolute;top:9px;left:9px;z-index:2;opacity:0;transition:opacity .15s}
.file-card:hover .file-check,.file-card.selected .file-check{opacity:1}
.file-check input{box-shadow:0 2px 8px rgba(0,0,0,.2)}
.star-btn{position:absolute;top:9px;right:9px;z-index:2;background:rgba(255,255,255,.86);border:0;border-radius:8px;width:28px;height:28px;display:grid;place-items:center;cursor:pointer;color:var(--muted);opacity:0;transition:all .15s;box-shadow:0 2px 8px rgba(0,0,0,.12)}
[data-theme="dark"] .star-btn{background:rgba(16,24,39,.86)}
.file-card:hover .star-btn,.file-card .star-btn.on{opacity:1}
.star-btn.on{color:#f59e0b}
.star-btn:hover{transform:scale(1.08)}

/* ---------- file list ---------- */
.file-list{display:flex;flex-direction:column;border:1px solid var(--border);border-radius:13px;overflow:hidden;background:var(--surface)}
.file-row{display:flex;align-items:center;gap:11px;padding:10px 14px;border-bottom:1px solid var(--border);transition:background .15s;min-width:0}
.file-row:last-child{border-bottom:0}
.file-row:hover{background:var(--surface-2)}
.file-row.selected{background:rgba(var(--accent-rgb),.06)}
.file-row .file-ico{width:34px;height:34px;border-radius:9px;background:var(--bg-2);display:grid;place-items:center;color:var(--accent);flex-shrink:0}
.file-row .grow{min-width:0}
.file-row .fname{font-size:12.5px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.file-row .fmeta{font-size:10.5px;color:var(--muted);margin-top:2px;display:flex;gap:6px;flex-wrap:wrap}
.file-row .factions{display:flex;gap:3px;opacity:0;transition:opacity .15s}
.file-row:hover .factions{opacity:1}
.file-row .factions button,.file-row .factions a{background:transparent;border:0;color:var(--muted);width:28px;height:28px;border-radius:7px;display:grid;place-items:center;cursor:pointer}
.file-row .factions button:hover,.file-row .factions a:hover{background:var(--bg-2);color:var(--accent)}
.file-row .fsize{font-size:11px;color:var(--muted);white-space:nowrap;width:70px;text-align:right}
.file-row .fdate{font-size:11px;color:var(--muted);white-space:nowrap;width:100px;text-align:right}
#fileArea.file-list{display:flex;flex-direction:column;gap:0;border:1px solid var(--border);border-radius:13px;overflow:hidden;background:var(--surface)}
#fileArea.file-list .file-card{display:flex;align-items:center;gap:11px;padding:9px 13px;border:0;border-bottom:1px solid var(--border);border-radius:0;box-shadow:none;transform:none;animation:none;overflow:visible}
#fileArea.file-list .file-card:last-child{border-bottom:0}
#fileArea.file-list .file-card:hover{transform:none;background:var(--surface-2);box-shadow:none}
#fileArea.file-list .file-thumb,#fileArea.file-list .thumb-fallback{width:36px;height:36px;margin:0;flex-shrink:0;border-radius:9px}
#fileArea.file-list .thumb-fallback .icon{width:20px;height:20px}
#fileArea.file-list .file-name{flex:1;margin:0;min-width:0;font-size:12.5px}
#fileArea.file-list .file-meta{margin:0;width:190px;justify-content:flex-end}
#fileArea.file-list .file-actions{flex-shrink:0}
#fileArea.file-list .file-check{position:static;opacity:1;order:-1}
#fileArea.file-list .star-btn{position:static;opacity:1;order:-1;box-shadow:none;background:transparent}
#fileArea.file-list .file-name{order:0}
@media(max-width:600px){ #fileArea.file-list .file-meta{display:none}#fileArea.file-list .file-actions .btn span{display:none}}
.empty{text-align:center;padding:46px 18px;color:var(--muted)}
.empty .empty-ico{width:70px;height:70px;border-radius:20px;background:rgba(var(--accent-rgb),.08);color:var(--accent);display:grid;place-items:center;margin:0 auto 14px}
.empty h4{margin:0 0 5px;color:var(--text);font-size:15px}
.empty p{margin:0 auto;font-size:12.5px;max-width:340px;line-height:1.6}

/* ---------- bulk bar ---------- */
.bulk-bar{
  position:fixed;left:50%;bottom:22px;transform:translate(-50%,120%);z-index:180;
  display:flex;align-items:center;gap:8px;padding:9px 11px;border-radius:14px;
  background:var(--surface);border:1px solid var(--border);box-shadow:var(--shadow-lg);
  transition:transform .28s cubic-bezier(.4,1.4,.6,1);max-width:calc(100vw - 28px);flex-wrap:wrap;justify-content:center;
}
.bulk-bar.show{transform:translate(-50%,0)}
.bulk-bar .count{font-size:12px;font-weight:750;padding:0 7px;white-space:nowrap}
.bulk-bar .count span{color:var(--accent)}

/* ---------- badges ---------- */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:99px;font-size:10.5px;font-weight:700;letter-spacing:.01em;white-space:nowrap}
.badge-ok{background:rgba(16,185,129,.12);color:#059669}
.badge-fail{background:rgba(239,68,68,.12);color:#dc2626}
.badge-warn{background:rgba(245,158,11,.14);color:#b45309}
.badge-info{background:rgba(14,165,233,.12);color:#0284c7}
.badge-admin{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff}
.badge-user{background:var(--bg-2);color:var(--muted)}
.badge-stars{background:rgba(245,158,11,.14);color:#b45309}
[data-theme="dark"] .badge-ok{color:#34d399}
[data-theme="dark"] .badge-fail{color:#f87171}
[data-theme="dark"] .badge-warn{color:#fbbf24}

/* ---------- tables ---------- */
.table-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 12px;border-bottom:1px solid var(--border);text-align:left;font-size:12.5px;vertical-align:middle}
th{font-weight:700;color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;background:var(--surface-2);white-space:nowrap}
tr:last-child td{border-bottom:0}
tbody tr{transition:background .15s}
tbody tr:hover{background:var(--surface-2)}
td .flex{gap:5px;flex-wrap:nowrap}
td form{display:inline}

/* ---------- flash / toast ---------- */
.flash-stack{display:flex;flex-direction:column;gap:8px;margin-bottom:16px}
.flash{padding:11px 14px;border-radius:11px;font-size:12.5px;font-weight:600;border:1px solid;display:flex;align-items:center;gap:8px;animation:rise .3s ease}
.flash.ok{background:rgba(16,185,129,.09);color:#047857;border-color:rgba(16,185,129,.22)}
.flash.err{background:rgba(239,68,68,.09);color:#b91c1c;border-color:rgba(239,68,68,.22)}
.flash.warn{background:rgba(245,158,11,.1);color:#b45309;border-color:rgba(245,158,11,.22)}
.flash.info{background:rgba(14,165,233,.09);color:#0369a1;border-color:rgba(14,165,233,.22)}
[data-theme="dark"] .flash.ok{color:#6ee7b7}
[data-theme="dark"] .flash.err{color:#fca5a5}
[data-theme="dark"] .flash.warn{color:#fcd34d}
[data-theme="dark"] .flash.info{color:#7dd3fc}
.toasts{position:fixed;right:20px;bottom:20px;z-index:400;display:flex;flex-direction:column;gap:8px;max-width:360px}
.toast{
  padding:11px 14px;border-radius:11px;font-size:12.5px;font-weight:600;
  box-shadow:var(--shadow-lg);border:1px solid;background:var(--surface);
  animation:toastIn .32s cubic-bezier(.4,1.4,.6,1);display:flex;align-items:center;gap:9px;
}
.toast.out{animation:toastOut .25s forwards}
@keyframes toastIn{from{opacity:0;transform:translateX(30px)}to{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateX(30px)}}
.toast-success{border-color:rgba(16,185,129,.3);color:#047857}
.toast-error{border-color:rgba(239,68,68,.3);color:#b91c1c}
.toast-warn{border-color:rgba(245,158,11,.3);color:#b45309}
.toast-info{border-color:rgba(14,165,233,.3);color:#0369a1}
[data-theme="dark"] .toast-success{color:#6ee7b7}
[data-theme="dark"] .toast-error{color:#fca5a5}
[data-theme="dark"] .toast-warn{color:#fcd34d}
[data-theme="dark"] .toast-info{color:#7dd3fc}

/* ---------- modal / preview ---------- */
.modal-backdrop{
  position:fixed;inset:0;background:rgba(5,10,20,.55);backdrop-filter:blur(6px);
  z-index:300;display:none;align-items:center;justify-content:center;padding:18px;
}
.modal-backdrop.show{display:flex;animation:fadeIn .18s}
.modal{
  background:var(--surface);border:1px solid var(--border);border-radius:18px;
  width:100%;max-width:560px;max-height:92vh;overflow:auto;box-shadow:var(--shadow-lg);
  animation:modalIn .28s cubic-bezier(.4,1.3,.6,1);position:relative;
}
@keyframes modalIn{from{opacity:0;transform:scale(.96) translateY(12px)}to{opacity:1;transform:none}}
.modal-head{display:flex;align-items:center;gap:10px;padding:16px 18px;border-bottom:1px solid var(--border);position:sticky;top:0;background:var(--surface);z-index:2;border-radius:18px 18px 0 0}
.modal-head h3{margin:0;font-size:14.5px;font-weight:750;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.modal-body{padding:18px}
.modal-foot{padding:14px 18px;border-top:1px solid var(--border);display:flex;gap:9px;justify-content:flex-end;flex-wrap:wrap}
.preview-shell{
  background:var(--surface);border:1px solid var(--border);border-radius:18px;width:100%;
  max-width:1000px;max-height:94vh;display:flex;flex-direction:column;box-shadow:var(--shadow-lg);
  animation:modalIn .28s cubic-bezier(.4,1.3,.6,1);overflow:hidden;
}
.preview-head{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border);flex-shrink:0}
.preview-head b{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.preview-body{flex:1;overflow:auto;display:grid;place-items:center;padding:14px;background:var(--bg-2);min-height:240px}
.preview-body img{max-width:100%;max-height:72vh;border-radius:10px;box-shadow:var(--shadow-lg)}
.preview-body video{max-width:100%;max-height:72vh;border-radius:10px;background:#000}
.preview-body audio{width:min(560px,100%)}
.preview-body iframe{width:100%;height:72vh;border:0;border-radius:10px;background:#fff}
.preview-body pre{width:100%;max-height:72vh;overflow:auto;margin:0;padding:16px;background:var(--surface);border-radius:10px;font-family:var(--mono);font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.preview-nav{display:flex;gap:6px}

/* ---------- command palette ---------- */
.palette{
  background:var(--surface);border:1px solid var(--border);border-radius:16px;
  width:100%;max-width:620px;box-shadow:var(--shadow-lg);overflow:hidden;
  animation:modalIn .22s cubic-bezier(.4,1.3,.6,1);
}
.palette-input{display:flex;align-items:center;gap:10px;padding:13px 15px;border-bottom:1px solid var(--border)}
.palette-input input{border:0;box-shadow:none;padding:0;font-size:14px;background:transparent}
.palette-results{max-height:min(420px,60vh);overflow:auto;padding:7px}
.palette-group{font-size:9.5px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;color:var(--muted-2);padding:8px 9px 4px}
.palette-item{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:9px;cursor:pointer;font-size:12.5px;color:var(--text);text-decoration:none}
.palette-item .icon{color:var(--muted)}
.palette-item .grow{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.palette-item .hint{font-size:10.5px}
.palette-item:hover,.palette-item.active{background:rgba(var(--accent-rgb),.1);color:var(--accent)}
.palette-item:hover .icon,.palette-item.active .icon{color:var(--accent)}
.palette-foot{padding:8px 14px;border-top:1px solid var(--border);display:flex;gap:12px;font-size:10.5px;color:var(--muted-2);flex-wrap:wrap}
.palette-foot span{display:flex;align-items:center;gap:4px}

/* ---------- context menu ---------- */
.ctx-menu{
  position:fixed;z-index:350;min-width:190px;background:var(--surface);border:1px solid var(--border);
  border-radius:11px;box-shadow:var(--shadow-lg);padding:5px;display:none;
}
.ctx-menu.show{display:block;animation:popIn .14s ease}
.ctx-menu button{display:flex;align-items:center;gap:9px;width:100%;padding:8px 9px;border:0;border-radius:7px;background:transparent;color:var(--text);font-size:12px;font-weight:600;cursor:pointer;text-align:left}
.ctx-menu button:hover{background:var(--bg-2);color:var(--accent)}
.ctx-menu button.danger{color:#dc2626}
.ctx-menu button.danger:hover{background:rgba(239,68,68,.1)}
.ctx-menu .sep{height:1px;background:var(--border);margin:4px}

/* ---------- charts ---------- */
.chart-bars{display:flex;align-items:flex-end;gap:7px;height:170px;padding:8px 0 0;border-bottom:1px solid var(--border)}
.bar-wrap{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end;gap:6px;min-width:0}
.bar{
  width:100%;max-width:44px;background:linear-gradient(180deg,var(--accent),var(--accent-2));
  border-radius:6px 6px 2px 2px;min-height:2px;position:relative;transition:height .6s cubic-bezier(.4,0,.2,1);
}
.bar:hover::after{content:attr(data-count);position:absolute;top:-24px;left:50%;transform:translateX(-50%);background:var(--text);color:var(--bg);padding:2px 7px;border-radius:5px;font-size:10px;white-space:nowrap}
.bar-label{font-size:10px;color:var(--muted);font-weight:600}
.donut-wrap{display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.donut{width:128px;height:128px;transform:rotate(-90deg);flex-shrink:0}
.donut circle{transition:stroke-dashoffset .8s cubic-bezier(.4,0,.2,1)}
.legend{display:flex;flex-direction:column;gap:6px;font-size:12px;min-width:150px}
.legend-item{display:flex;align-items:center;gap:8px}
.legend-dot{width:9px;height:9px;border-radius:3px;flex-shrink:0}
.legend-item .grow{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.legend-item .pct{color:var(--muted);font-size:11px}
.ring-wrap{display:flex;align-items:center;gap:16px}
.ring{transform:rotate(-90deg)}
.ring circle{transition:stroke-dashoffset .8s cubic-bezier(.4,0,.2,1)}

/* ---------- tabs ---------- */
.tabs{display:flex;gap:4px;background:var(--bg-2);border:1px solid var(--border);border-radius:11px;padding:4px;flex-wrap:wrap;margin-bottom:18px}
.tabs a{display:flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;font-size:12px;font-weight:650;color:var(--muted);text-decoration:none}
.tabs a:hover{color:var(--text)}
.tabs a.active{background:var(--surface);color:var(--accent);box-shadow:0 1px 5px rgba(0,0,0,.08)}

/* ---------- auth ---------- */
.auth-wrap{
  min-height:100vh;display:grid;place-items:center;padding:26px;
  background:
    radial-gradient(circle at 12% 8%,rgba(var(--accent-rgb),.16),transparent 42%),
    radial-gradient(circle at 88% 92%,rgba(var(--accent-rgb),.12),transparent 44%),
    var(--bg);
}
.auth-card{
  width:100%;max-width:410px;background:var(--surface);border:1px solid var(--border);
  border-radius:20px;padding:34px 30px;box-shadow:var(--shadow-lg);animation:rise .4s ease;
}
.auth-brand{display:flex;align-items:center;gap:12px;justify-content:center;margin-bottom:24px}
.auth-brand .brand-logo{width:50px;height:50px;font-size:22px;border-radius:14px}
.auth-brand h1{margin:0;font-size:16px;font-weight:800;letter-spacing:-.02em}
.auth-brand span{display:block;font-size:10.5px;color:var(--muted);font-weight:600;margin-top:2px}
.auth-card h2{margin:0 0 6px;font-size:22px;font-weight:800;letter-spacing:-.03em}
.auth-card .sub{color:var(--muted);font-size:12.5px;margin:0 0 22px;line-height:1.5}
.auth-card .field{margin-bottom:12px}
.auth-card .btn{width:100%;padding:11px;margin-top:4px}
.auth-links{text-align:center;margin-top:18px;font-size:11.5px;color:var(--muted)}
.auth-links a{font-weight:650}

/* ---------- landing ---------- */
.landing{min-height:100vh;display:flex;flex-direction:column;overflow:hidden}
.landing-nav{
  display:flex;align-items:center;gap:16px;padding:16px 5vw;position:sticky;top:0;z-index:100;
  border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--bg) 76%,transparent);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
}
.landing-brand{display:flex;align-items:center;gap:11px;text-decoration:none;color:var(--text)}
.landing-brand .brand-logo{width:38px;height:38px;border-radius:11px;font-size:17px}
.landing-brand b{font-size:14px;font-weight:800;letter-spacing:-.02em}
.landing-brand span{display:block;font-size:10px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.landing-links{margin-left:auto;display:flex;align-items:center;gap:6px}
.landing-links a{font-size:12.5px;font-weight:650;color:var(--muted);padding:8px 10px;border-radius:8px}
.landing-links a:hover{color:var(--text);background:var(--bg-2)}
.hero{padding:clamp(60px,10vw,120px) 5vw 60px;text-align:center;max-width:980px;margin:0 auto;position:relative}
.hero::before{
  content:"";position:absolute;inset:-40% 10% auto;height:520px;z-index:-1;
  background:radial-gradient(circle at 50% 40%,rgba(var(--accent-rgb),.16),transparent 65%);
  filter:blur(20px);pointer-events:none;
}
.hero-badge{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:99px;border:1px solid rgba(var(--accent-rgb),.22);background:rgba(var(--accent-rgb),.07);color:var(--accent);font-size:11px;font-weight:700;margin-bottom:20px}
.hero h1{font-size:clamp(34px,6vw,64px);line-height:1.02;letter-spacing:-.045em;margin:0 0 18px;font-weight:850}
.hero h1 .grad{background:linear-gradient(120deg,var(--accent),var(--accent-2),#ec4899);-webkit-background-clip:text;background-clip:text;color:transparent}
.hero p{font-size:clamp(14px,1.8vw,17px);color:var(--muted);line-height:1.65;max-width:620px;margin:0 auto 28px}
.hero-actions{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.hero-actions .btn{padding:12px 22px;font-size:13.5px;border-radius:11px}
.hero-stats{display:flex;gap:28px;justify-content:center;flex-wrap:wrap;margin-top:44px;padding-top:28px;border-top:1px solid var(--border);max-width:620px;margin-left:auto;margin-right:auto}
.hero-stat b{display:block;font-size:22px;font-weight:800;letter-spacing:-.02em}
.hero-stat span{font-size:10.5px;color:var(--muted);font-weight:650;text-transform:uppercase;letter-spacing:.07em}
.landing-section{padding:60px 5vw;max-width:1180px;margin:0 auto;width:100%}
.landing-section h2{text-align:center;font-size:clamp(24px,3.4vw,38px);letter-spacing:-.035em;margin:0 0 10px;font-weight:850}
.landing-section .lead{text-align:center;color:var(--muted);font-size:13.5px;max-width:560px;margin:0 auto 38px;line-height:1.6}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:15px}
.feature{
  background:var(--surface);border:1px solid var(--border);border-radius:15px;padding:22px;
  transition:all .22s;box-shadow:var(--shadow);
}
.feature:hover{transform:translateY(-4px);box-shadow:var(--shadow-lg);border-color:rgba(var(--accent-rgb),.3)}
.feature .f-ico{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;background:rgba(var(--accent-rgb),.1);color:var(--accent);margin-bottom:14px}
.feature h3{margin:0 0 6px;font-size:14px;font-weight:750;letter-spacing:-.01em}
.feature p{margin:0;color:var(--muted);font-size:12.5px;line-height:1.6}
.showcase{
  display:grid;grid-template-columns:1.1fr .9fr;gap:34px;align-items:center;
  background:var(--surface);border:1px solid var(--border);border-radius:22px;padding:36px;box-shadow:var(--shadow-lg);
}
.showcase h2{text-align:left;margin-bottom:10px}
.showcase .lead{text-align:left;margin:0 0 22px}
.check-list{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:11px}
.check-list li{display:flex;gap:10px;align-items:flex-start;font-size:12.5px;color:var(--muted);line-height:1.5}
.check-list .icon{color:var(--success);margin-top:2px;flex-shrink:0}
.showcase-visual{
  aspect-ratio:1.25;border-radius:16px;background:
    radial-gradient(circle at 25% 20%,rgba(var(--accent-rgb),.22),transparent 55%),
    linear-gradient(135deg,rgba(var(--accent-rgb),.1),rgba(236,72,153,.1));
  border:1px solid rgba(var(--accent-rgb),.18);display:grid;place-items:center;color:var(--accent);position:relative;overflow:hidden;
}
.showcase-visual .float-card{position:absolute;background:var(--surface);border:1px solid var(--border);border-radius:11px;padding:9px 12px;box-shadow:var(--shadow);display:flex;align-items:center;gap:8px;font-size:11px;font-weight:650;animation:float 5s ease-in-out infinite}
.showcase-visual .fc1{top:18%;left:10%}
.showcase-visual .fc2{bottom:22%;right:9%;animation-delay:-2s}
.showcase-visual .fc3{bottom:12%;left:16%;animation-delay:-3.5s}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-9px)}}
.cta-band{
  margin:20px 5vw 70px;padding:44px 30px;border-radius:22px;text-align:center;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;
  box-shadow:0 20px 50px rgba(var(--accent-rgb),.3);
}
.cta-band h2{font-size:clamp(24px,3.2vw,34px);margin:0 0 8px;letter-spacing:-.03em;font-weight:850}
.cta-band p{margin:0 auto 20px;opacity:.9;font-size:13px;max-width:500px;line-height:1.6}
.cta-band .btn{background:#fff;color:var(--accent);box-shadow:0 8px 24px rgba(0,0,0,.16)}
.landing-footer{padding:26px 5vw;border-top:1px solid var(--border);display:flex;align-items:center;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11.5px;margin-top:auto}
.landing-footer .spacer{flex:1}

/* ---------- public share ---------- */
.share-page{min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 50% 0%,rgba(var(--accent-rgb),.14),transparent 55%),var(--bg)}
.share-card{width:100%;max-width:640px;background:var(--surface);border:1px solid var(--border);border-radius:20px;box-shadow:var(--shadow-lg);overflow:hidden}
.share-head{padding:22px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:13px}
.share-head .f-ico{width:50px;height:50px;border-radius:13px;background:rgba(var(--accent-rgb),.1);color:var(--accent);display:grid;place-items:center;flex-shrink:0}
.share-head h1{margin:0;font-size:16px;font-weight:800;word-break:break-word}
.share-head p{margin:3px 0 0;font-size:11.5px;color:var(--muted)}
.share-preview{padding:18px;background:var(--bg-2);display:grid;place-items:center;min-height:180px}
.share-preview img{max-width:100%;max-height:420px;border-radius:10px}
.share-preview iframe{width:100%;height:420px;border:0;border-radius:10px;background:#fff}
.share-body{padding:20px}
.share-meta{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.share-meta .item{background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.share-meta .item span{display:block;font-size:9.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;font-weight:700}
.share-meta .item b{display:block;font-size:12px;margin-top:2px}

/* ---------- misc ---------- */
.pagination{display:flex;align-items:center;gap:6px;justify-content:center;margin-top:16px;flex-wrap:wrap}
.pagination a,.pagination span{padding:7px 11px;border-radius:8px;border:1px solid var(--border);font-size:12px;font-weight:650;color:var(--muted);background:var(--surface)}
.pagination a:hover{border-color:var(--accent);color:var(--accent)}
.pagination .current{background:var(--accent);border-color:var(--accent);color:#fff}
.mobile-nav{display:none}
.spin{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.skeleton{background:linear-gradient(90deg,var(--bg-2) 25%,var(--surface-2) 50%,var(--bg-2) 75%);background-size:200% 100%;animation:shimmer 1.2s infinite;border-radius:8px}
@keyframes shimmer{to{background-position:-200% 0}}
.no-scroll{overflow:hidden}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.text-danger{color:var(--danger)}
.text-success{color:var(--success)}
.text-warn{color:var(--warning)}
.mt-0{margin-top:0}.mb-0{margin-bottom:0}.mt-1{margin-top:8px}.mb-1{margin-bottom:8px}
.w-full{width:100%}

/* ---------- responsive ---------- */
@media (max-width:1100px){
  .grid-3{grid-template-columns:1fr 1fr}
  .showcase{grid-template-columns:1fr;padding:28px}
}
@media (max-width:900px){
  :root{--sidebar:0px}
  .sidebar{transform:translateX(-100%);width:280px;box-shadow:var(--shadow-lg)}
  .sidebar.open{transform:translateX(0)}
  .topbar{left:0;padding:0 14px}
  .hamburger{display:inline-grid}
  .main{margin-left:0;padding:82px 14px 100px}
  .main.full{padding:0}
  .topbar-search{min-width:0;width:38px;padding:0;justify-content:center}
  .topbar-search .grow,.topbar-search kbd,.topbar-search .search-text{display:none}
  .topbar-title small{display:none}
  .grid-2,.grid-3{grid-template-columns:1fr}
  .file-grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .folder-grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}
  .mobile-nav{
    display:flex;position:fixed;left:10px;right:10px;bottom:10px;z-index:150;
    background:color-mix(in srgb,var(--surface) 92%,transparent);backdrop-filter:blur(14px);
    border:1px solid var(--border);border-radius:16px;padding:7px;box-shadow:var(--shadow-lg);
    justify-content:space-around;
  }
  .mobile-nav a{display:flex;flex-direction:column;align-items:center;gap:3px;font-size:9.5px;font-weight:700;color:var(--muted);padding:6px 10px;border-radius:10px;text-decoration:none}
  .mobile-nav a.active{color:var(--accent);background:rgba(var(--accent-rgb),.1)}
  .bulk-bar{bottom:80px}
  .toasts{left:14px;right:14px;bottom:86px;max-width:none}
  .share-meta{grid-template-columns:1fr}
}
@media (max-width:600px){
  .main{padding:78px 11px 96px}
  .card{padding:16px;border-radius:14px}
  .stat-grid{grid-template-columns:1fr 1fr;gap:9px}
  .stat{padding:12px;gap:10px;border-radius:12px}
  .stat-ico{width:38px;height:38px;border-radius:10px}
  .stat-val{font-size:17px}
  .stat-label{font-size:10px}
  .toolbar input,.toolbar select{min-width:0;width:100%}
  .toolbar form{width:100%}
  .toolbar form .btn{flex:1}
  .file-grid{grid-template-columns:repeat(auto-fill,minmax(135px,1fr));gap:9px}
  .file-thumb,.thumb-fallback{height:88px}
  .file-actions .btn span{display:none}
  .file-row .fdate{display:none}
  .file-row .fsize{width:56px}
  .landing-links a:not(.btn){display:none}
  .hero{padding-top:48px}
  .hero-stats{gap:18px}
  .landing-section{padding:42px 16px}
  .cta-band{margin:10px 14px 50px;padding:32px 18px}
  .auth-card{padding:28px 22px}
  .modal-body{padding:14px}
  .form-grid{grid-template-columns:1fr}
  .dropdown-menu{right:-6px}
  .hide-sm{display:none!important}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important}
}
'''

# ============================================================
# Base layout
# ============================================================
BASE = r'''<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#6366f1">
<meta name="description" content="DRZ File Manager — a fast, private and beautiful file manager with folders, previews, sharing and analytics.">
<title>{{ page_title or app_name }}</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="manifest" href="/manifest.webmanifest">
<script>
(function(){
  try{
    var t = localStorage.getItem('drz-theme') || '{{ theme }}' || 'system';
    if(t === 'system' || !t){ t = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
    document.documentElement.setAttribute('data-theme', t);
    var a = localStorage.getItem('drz-accent') || '{{ accent }}' || 'indigo';
    var map = {indigo:['#6366f1','#8b5cf6','99,102,241'],violet:['#8b5cf6','#d946ef','139,92,246'],emerald:['#10b981','#14b8a6','16,185,129'],rose:['#f43f5e','#ec4899','244,63,94'],amber:['#f59e0b','#f97316','245,158,11'],cyan:['#06b6d4','#0ea5e9','6,182,212']};
    var c = map[a] || map.indigo;
    document.documentElement.style.setProperty('--accent', c[0]);
    document.documentElement.style.setProperty('--accent-2', c[1]);
    document.documentElement.style.setProperty('--accent-rgb', c[2]);
  }catch(e){}
})();
</script>
<style>%%CSS%%</style>
</head>
<body>
{% if g.user %}
<aside class="sidebar" id="sidebar">
  <a class="brand" href="{{ url_for('index') }}">
    <div class="brand-logo">D</div>
    <div class="brand-text">{{ app_name }}<span>Ultra file manager</span></div>
  </a>
  <button class="sidebar-search" type="button" onclick="openPalette()">
    {{ icon('search', 15) }}<span class="grow">Search anything</span><kbd>Ctrl K</kbd>
  </button>
  <nav class="nav">
    <div class="nav-label">Workspace</div>
    <a href="{{ url_for('index') }}" class="nav-item {% if request.endpoint=='index' %}active{% endif %}"><span class="nav-icon">{{ icon('files', 18) }}</span>All files</a>
    <a href="{{ url_for('starred') }}" class="nav-item {% if request.endpoint=='starred' %}active{% endif %}"><span class="nav-icon">{{ icon('star', 18) }}</span>Starred</a>
    <a href="{{ url_for('recent') }}" class="nav-item {% if request.endpoint=='recent' %}active{% endif %}"><span class="nav-icon">{{ icon('clock', 18) }}</span>Recent</a>
    <a href="{{ url_for('shares_view') }}" class="nav-item {% if request.endpoint=='shares_view' %}active{% endif %}"><span class="nav-icon">{{ icon('share', 18) }}</span>Shares</a>
    <a href="{{ url_for('trash_view') }}" class="nav-item {% if request.endpoint=='trash_view' %}active{% endif %}"><span class="nav-icon">{{ icon('trash', 18) }}</span>Trash {% if trash_count %}<span class="nav-count">{{ trash_count }}</span>{% endif %}</a>
    <div class="nav-label">Account</div>
    <a href="{{ url_for('account') }}" class="nav-item {% if request.endpoint=='account' %}active{% endif %}"><span class="nav-icon">{{ icon('user', 18) }}</span>Profile &amp; settings</a>
    {% if g.user['is_admin'] %}
    <a href="{{ url_for('admin_home') }}" class="nav-item {% if request.endpoint in ['admin_home','admin_logs','admin_monitor','admin_settings','admin_change_pin','admin_pin'] %}active{% endif %}"><span class="nav-icon">{{ icon('shield', 18) }}</span>Admin panel</a>
    {% endif %}
  </nav>
  <div class="sidebar-storage">
    <div class="storage-head"><span>Storage</span><b>{{ storage_used|filesize }}{% if storage_quota %} / {{ storage_quota|filesize }}{% endif %}</b></div>
    <div class="meter"><span style="width:{{ storage_pct }}%"></span></div>
    <div class="storage-foot"><span>{{ storage_pct }}% used</span><span>{{ file_count }} files</span></div>
  </div>
  <div class="sidebar-footer">
    <div class="sidebar-user">
      <div class="avatar">{{ (g.user['display_name'] or g.user['username'])[0]|upper }}</div>
      <div class="grow" style="min-width:0"><b>{{ g.user['display_name'] or g.user['username'] }}</b><span>{{ 'Administrator' if g.user['is_admin'] else 'Member' }}</span></div>
    </div>
    <button class="icon-btn small theme-btn" type="button" onclick="toggleTheme()" title="Toggle theme" aria-label="Toggle theme">{{ icon('sun', 16) }}</button>
    <form method="post" action="{{ url_for('logout') }}" style="margin:0">
      <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
      <button class="icon-btn small" type="submit" title="Sign out" aria-label="Sign out">{{ icon('logout', 16) }}</button>
    </form>
  </div>
</aside>

<header class="topbar">
  <button class="icon-btn hamburger" type="button" onclick="document.getElementById('sidebar').classList.toggle('open')" aria-label="Open menu">{{ icon('menu', 19) }}</button>
  <div class="topbar-title">{{ page_title or 'Files' }}<small>{{ app_name }}</small></div>
  <div class="topbar-actions">
    <button class="topbar-search" type="button" onclick="openPalette()">{{ icon('search', 15) }}<span class="grow search-text">Search files &amp; actions</span><kbd>Ctrl K</kbd></button>
    <div class="dropdown" id="accentDropdown">
      <button class="icon-btn" type="button" onclick="toggleDropdown('accentDropdown')" title="Accent color" aria-label="Accent color">{{ icon('palette', 17) }}</button>
      <div class="dropdown-menu" style="min-width:0">
        <div class="accent-dots">
          <button class="accent-dot" data-accent="indigo" style="background:#6366f1" title="Indigo" onclick="applyAccent('indigo')"></button>
          <button class="accent-dot" data-accent="violet" style="background:#8b5cf6" title="Violet" onclick="applyAccent('violet')"></button>
          <button class="accent-dot" data-accent="emerald" style="background:#10b981" title="Emerald" onclick="applyAccent('emerald')"></button>
          <button class="accent-dot" data-accent="rose" style="background:#f43f5e" title="Rose" onclick="applyAccent('rose')"></button>
          <button class="accent-dot" data-accent="amber" style="background:#f59e0b" title="Amber" onclick="applyAccent('amber')"></button>
          <button class="accent-dot" data-accent="cyan" style="background:#06b6d4" title="Cyan" onclick="applyAccent('cyan')"></button>
        </div>
      </div>
    </div>
    <button class="icon-btn theme-btn" type="button" onclick="toggleTheme()" title="Toggle theme" aria-label="Toggle theme">{{ icon('moon', 17) }}</button>
    <div class="dropdown" id="userDropdown">
      <button class="avatar" type="button" onclick="toggleDropdown('userDropdown')" title="Account menu" aria-label="Account menu">{{ (g.user['display_name'] or g.user['username'])[0]|upper }}</button>
      <div class="dropdown-menu">
        <div style="padding:9px 10px 7px">
          <div style="font-size:12.5px;font-weight:750">{{ g.user['display_name'] or g.user['username'] }}</div>
          <div class="hint" style="font-size:10.5px">@{{ g.user['username'] }}{% if g.user['is_admin'] %} · admin{% endif %}</div>
        </div>
        <div class="sep"></div>
        <a href="{{ url_for('account') }}">{{ icon('user', 15) }} Account settings</a>
        {% if g.user['is_admin'] %}<a href="{{ url_for('admin_home') }}">{{ icon('shield', 15) }} Admin panel</a>{% endif %}
        <a href="{{ url_for('shares_view') }}">{{ icon('share', 15) }} My share links</a>
        <div class="sep"></div>
        <form method="post" action="{{ url_for('logout') }}" style="margin:0">
          <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
          <button type="submit">{{ icon('logout', 15) }} Sign out</button>
        </form>
      </div>
    </div>
  </div>
</header>
{% endif %}

<main class="main {% if not g.user %}full{% endif %}">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      <div class="flash-stack">
      {% for cat, msg in messages %}<div class="flash {{ cat }}">{{ icon('check-circle' if cat=='ok' else ('alert' if cat in ['err','warn'] else 'info'), 16) }} {{ msg }}</div>{% endfor %}
      </div>
    {% endif %}
  {% endwith %}
  %%BODY%%
</main>

{% if g.user %}
<nav class="mobile-nav" aria-label="Mobile navigation">
  <a href="{{ url_for('index') }}" class="{% if request.endpoint=='index' %}active{% endif %}">{{ icon('files', 19) }}Files</a>
  <a href="{{ url_for('starred') }}" class="{% if request.endpoint=='starred' %}active{% endif %}">{{ icon('star', 19) }}Starred</a>
  <a href="{{ url_for('shares_view') }}" class="{% if request.endpoint=='shares_view' %}active{% endif %}">{{ icon('share', 19) }}Shares</a>
  <a href="{{ url_for('trash_view') }}" class="{% if request.endpoint=='trash_view' %}active{% endif %}">{{ icon('trash', 19) }}Trash</a>
  <a href="{{ url_for('account') }}" class="{% if request.endpoint=='account' %}active{% endif %}">{{ icon('user', 19) }}Account</a>
</nav>
{% endif %}

<div class="modal-backdrop" id="paletteModal" onclick="if(event.target===this)closePalette()">
  <div class="palette" role="dialog" aria-label="Command palette">
    <div class="palette-input">{{ icon('search', 18) }}<input id="paletteInput" placeholder="Search files, folders or type a command…" autocomplete="off" spellcheck="false"><kbd>Esc</kbd></div>
    <div class="palette-results" id="paletteResults"></div>
    <div class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div>
  </div>
</div>

<div class="modal-backdrop" id="previewModal" onclick="if(event.target===this)closePreview()">
  <div class="preview-shell">
    <div class="preview-head">
      {{ icon('eye', 17) }}<b id="previewName">Preview</b>
      <div class="preview-nav">
        <a class="icon-btn small" id="previewDownload" href="#" title="Download">{{ icon('download', 16) }}</a>
        <button class="icon-btn small" type="button" onclick="closePreview()" title="Close">{{ icon('x', 16) }}</button>
      </div>
    </div>
    <div class="preview-body" id="previewBody"></div>
  </div>
</div>

<div class="modal-backdrop" id="moveModal" onclick="if(event.target===this)closeModal('moveModal')">
  <div class="modal" role="dialog" aria-label="Move file">
    <div class="modal-head">{{ icon('move', 17) }}<h3>Move to folder</h3><button class="icon-btn small" type="button" onclick="closeModal('moveModal')">{{ icon('x', 15) }}</button></div>
    <form method="post" id="moveForm">
      <div class="modal-body">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
        <input type="hidden" name="ids" id="moveIds">
        <div class="field"><label>Destination</label><select name="folder_id" id="moveFolder"></select></div>
        <p class="hint" style="margin:10px 0 0">Choose “Home” to move items to the root of your workspace.</p>
      </div>
      <div class="modal-foot"><button class="btn ghost" type="button" onclick="closeModal('moveModal')">Cancel</button><button class="btn" type="submit">{{ icon('move', 14) }} Move</button></div>
    </form>
  </div>
</div>

<div class="modal-backdrop" id="shareModal" onclick="if(event.target===this)closeModal('shareModal')">
  <div class="modal" role="dialog" aria-label="Share file">
    <div class="modal-head">{{ icon('share', 17) }}<h3 id="shareTitle">Share file</h3><button class="icon-btn small" type="button" onclick="closeModal('shareModal')">{{ icon('x', 15) }}</button></div>
    <form method="post" id="shareForm">
      <div class="modal-body">
        <input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
        <div class="form-grid">
          <div class="field"><label>Expires in</label><select name="hours"><option value="1">1 hour</option><option value="6">6 hours</option><option value="24" selected>24 hours</option><option value="72">3 days</option><option value="168">7 days</option><option value="720">30 days</option></select></div>
          <div class="field"><label>Max downloads (0 = unlimited)</label><input type="number" name="max_downloads" min="0" max="100000" value="0"></div>
          <div class="field full"><label>Password (optional)</label><input type="password" name="password" placeholder="Leave blank for a public link" autocomplete="new-password"></div>
          <div class="field full"><label>Note (optional)</label><input name="note" maxlength="120" placeholder="e.g. For the design team"></div>
          <label class="flex" style="gap:7px;font-weight:600"><input type="checkbox" name="allow_preview" value="1" checked> Allow inline preview</label>
        </div>
      </div>
      <div class="modal-foot"><button class="btn ghost" type="button" onclick="closeModal('shareModal')">Cancel</button><button class="btn" type="submit">{{ icon('link', 14) }} Create link</button></div>
    </form>
  </div>
</div>

<div class="modal-backdrop" id="detailsModal" onclick="if(event.target===this)closeModal('detailsModal')">
  <div class="modal" role="dialog" aria-label="File details">
    <div class="modal-head">{{ icon('info', 17) }}<h3>File details</h3><button class="icon-btn small" type="button" onclick="closeModal('detailsModal')">{{ icon('x', 15) }}</button></div>
    <div class="modal-body" id="detailsBody"></div>
  </div>
</div>

<div id="ctxMenu" class="ctx-menu"></div>
<div class="toasts" id="toasts"></div>

<script>
window.__DRZ = {
  csrf: "{{ csrf_token() }}",
  user: {% if g.user %}{id: {{ g.user['id'] }}, name: {{ (g.user['display_name'] or g.user['username'])|tojson }}}{% else %}null{% endif %},
  urls: {
    search: "{{ url_for('api_search') }}",
    upload: "{{ url_for('upload') }}",
    index: "{{ url_for('index') }}",
    preview: "/preview/",
    download: "/download/",
    star: "/file/",
    rename: "/file/",
    move: "/file/",
    share: "/share/",
    trash: "{{ url_for('trash_view') }}"
  },
  folders: [
    {id:'', name:'Home', depth:0}
    {% for f in all_folders %},{id:{{ f['id'] }}, name:{{ f['name']|tojson }}, depth:{{ f['depth'] }}}{% endfor %}
  ],
  currentFolder: {{ (current_folder or 0)|tojson }}
};
</script>
<script>%%JS%%</script>
</body>
</html>'''

# ============================================================
# Client-side application
# ============================================================
JS = r'''
(function(){
  'use strict';
  var D = window.__DRZ || {};
  var $ = function(s, r){ return (r||document).querySelector(s); };
  var $$ = function(s, r){ return Array.prototype.slice.call((r||document).querySelectorAll(s)); };
  var ACCENTS = {
    indigo:['#6366f1','#8b5cf6','99,102,241'], violet:['#8b5cf6','#d946ef','139,92,246'],
    emerald:['#10b981','#14b8a6','16,185,129'], rose:['#f43f5e','#ec4899','244,63,94'],
    amber:['#f59e0b','#f97316','245,158,11'], cyan:['#06b6d4','#0ea5e9','6,182,212']
  };

  /* ---------- theme / accent ---------- */
  window.toggleTheme = function(){
    var cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', cur);
    try{ localStorage.setItem('drz-theme', cur); }catch(e){}
    var meta = document.querySelector('meta[name="theme-color"]');
    if(meta) meta.setAttribute('content', cur === 'dark' ? '#080d19' : '#6366f1');
    toast(cur === 'dark' ? 'Dark mode enabled' : 'Light mode enabled', 'info');
  };
  window.applyAccent = function(name){
    var c = ACCENTS[name] || ACCENTS.indigo;
    document.documentElement.style.setProperty('--accent', c[0]);
    document.documentElement.style.setProperty('--accent-2', c[1]);
    document.documentElement.style.setProperty('--accent-rgb', c[2]);
    try{ localStorage.setItem('drz-accent', name); }catch(e){}
    $$('.accent-dot').forEach(function(el){ el.classList.toggle('active', el.dataset.accent === name); });
    toast('Accent updated', 'success');
  };
  window.toggleDropdown = function(id){
    var el = document.getElementById(id); if(!el) return;
    var was = el.classList.contains('open');
    $$('.dropdown.open').forEach(function(x){ x.classList.remove('open'); });
    if(!was) el.classList.add('open');
  };
  document.addEventListener('click', function(e){
    if(!e.target.closest('.dropdown')) $$('.dropdown.open').forEach(function(x){ x.classList.remove('open'); });
  });

  /* ---------- toasts ---------- */
  window.toast = function(msg, type){
    type = type || 'info';
    var box = document.getElementById('toasts'); if(!box) return;
    var el = document.createElement('div');
    el.className = 'toast toast-' + type;
    var icons = {success:'✓', error:'!', warn:'!', info:'i'};
    el.innerHTML = '<span style="width:18px;height:18px;border-radius:50%;display:grid;place-items:center;background:currentColor;color:var(--surface);font-size:11px;font-weight:800;flex-shrink:0">' + (icons[type]||'i') + '</span><span></span>';
    el.lastChild.textContent = msg;
    box.appendChild(el);
    setTimeout(function(){ el.classList.add('out'); setTimeout(function(){ el.remove(); }, 300); }, 3800);
  };
  window.copyText = function(text){
    var done = function(){ toast('Copied to clipboard', 'success'); };
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).then(done).catch(function(){ fallbackCopy(text, done); });
    } else fallbackCopy(text, done);
  };
  function fallbackCopy(text, done){
    var ta = document.createElement('textarea'); ta.value = text; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    try{ document.execCommand('copy'); done(); }catch(e){ toast('Copy failed', 'error'); }
    ta.remove();
  }

  /* ---------- relative time ---------- */
  function parseUTC(ts){
    if(!ts) return null;
    var s = String(ts);
    if(s.indexOf('T') === -1) s = s.replace(' ', 'T');
    if(s.slice(-1) !== 'Z') s += 'Z';
    var d = new Date(s); return isNaN(d.getTime()) ? null : d;
  }
  window.relTime = function(ts){
    var d = parseUTC(ts); if(!d) return ts || 'never';
    var sec = (Date.now() - d.getTime()) / 1000;
    if(sec < 0) return 'just now';
    if(sec < 60) return Math.floor(sec) + 's ago';
    if(sec < 3600) return Math.floor(sec/60) + 'm ago';
    if(sec < 86400) return Math.floor(sec/3600) + 'h ago';
    if(sec < 2592000) return Math.floor(sec/86400) + 'd ago';
    return d.toLocaleDateString();
  };
  function applyRelTimes(){
    $$('[data-rel]').forEach(function(el){ el.textContent = relTime(el.dataset.rel); });
  }

  /* ---------- modal helpers ---------- */
  window.openModal = function(id){ var m = document.getElementById(id); if(m){ m.classList.add('show'); document.body.classList.add('no-scroll'); } };
  window.closeModal = function(id){ var m = document.getElementById(id); if(m){ m.classList.remove('show'); document.body.classList.remove('no-scroll'); } };
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape'){
      $$('.modal-backdrop.show').forEach(function(m){ m.classList.remove('show'); });
      document.body.classList.remove('no-scroll');
      closePalette(); closePreview(); hideCtx();
    }
  });

  /* ---------- command palette ---------- */
  var paletteItems = [], paletteIndex = 0, paletteTimer = null;
  window.openPalette = function(){
    var m = document.getElementById('paletteModal'); if(!m) return;
    m.classList.add('show'); document.body.classList.add('no-scroll');
    var input = document.getElementById('paletteInput');
    input.value = ''; renderPalette('', null); input.focus();
  };
  window.closePalette = function(){
    var m = document.getElementById('paletteModal');
    if(m) m.classList.remove('show');
    document.body.classList.remove('no-scroll');
  };
  function actionItems(){
    return [
      {icon:'folder-plus', title:'New folder', hint:'Create a folder here', run:function(){ newFolder(); }},
      {icon:'upload', title:'Upload files', hint:'Open file picker', run:function(){ var fi=document.getElementById('fileInput'); if(fi) fi.click(); }},
      {icon:'star', title:'Starred files', hint:'View favorites', href:'/starred'},
      {icon:'share', title:'Share links', hint:'Manage shared files', href:'/shares'},
      {icon:'trash', title:'Trash', hint:'Restore or purge files', href:'/trash'},
      {icon:'user', title:'Account settings', hint:'Profile and password', href:'/account'},
      {icon:'moon', title:'Toggle theme', hint:'Light / dark mode', run:function(){ toggleTheme(); closePalette(); }},
      {icon:'palette', title:'Change accent color', hint:'Pick a new accent', run:function(){ closePalette(); toggleDropdown('accentDropdown'); }}
    ];
  }
  function renderPalette(q, data){
    var box = document.getElementById('paletteResults'); if(!box) return;
    var html = '';
    var items = [];
    var query = (q || '').trim().toLowerCase();
    if(!query){
      html += '<div class="palette-group">Quick actions</div>';
      actionItems().forEach(function(it){
        var idx = items.length; items.push(it);
        html += '<a class="palette-item" data-idx="' + idx + '" href="' + (it.href || '#') + '">' + iconSvg(it.icon) + '<span class="grow">' + esc(it.title) + '</span><span class="hint">' + esc(it.hint) + '</span></a>';
      });
    }
    if(data){
      if(data.folders && data.folders.length){
        html += '<div class="palette-group">Folders</div>';
        data.folders.forEach(function(f){
          var idx = items.length; items.push({href:'/?folder=' + f.id});
          html += '<a class="palette-item" data-idx="' + idx + '" href="/?folder=' + f.id + '">' + iconSvg('folder') + '<span class="grow">' + esc(f.name) + '</span><span class="hint">folder</span></a>';
        });
      }
      if(data.files && data.files.length){
        html += '<div class="palette-group">Files</div>';
        data.files.forEach(function(f){
          var idx = items.length;
          items.push({file:f});
          html += '<a class="palette-item" data-idx="' + idx + '" href="#" data-file="' + f.id + '">' + iconSvg(f.kind === 'image' ? 'image' : (f.kind === 'video' ? 'video' : (f.kind === 'audio' ? 'audio' : 'file'))) + '<span class="grow">' + esc(f.name) + '</span><span class="hint">' + esc(f.size) + '</span></a>';
        });
      }
      if(!html) html = '<div class="empty" style="padding:28px"><p>No matches. Try another search.</p></div>';
    }
    box.innerHTML = html;
    paletteItems = items; paletteIndex = 0;
    $$('.palette-item', box).forEach(function(el){
      el.addEventListener('mouseenter', function(){ paletteIndex = parseInt(el.dataset.idx,10) || 0; highlight(); });
      el.addEventListener('click', function(e){
        var it = items[parseInt(el.dataset.idx,10) || 0];
        if(it && it.file){ e.preventDefault(); openPreview(it.file.id, it.file.name, it.file.kind); closePalette(); }
        else if(it && it.run){ e.preventDefault(); it.run(); }
        else if(it && it.href){ closePalette(); }
      });
    });
    highlight();
  }
  function highlight(){
    $$('.palette-item').forEach(function(el, i){ el.classList.toggle('active', i === paletteIndex); });
    var active = $$('.palette-item')[paletteIndex];
    if(active && active.scrollIntoView) active.scrollIntoView({block:'nearest'});
  }
  function iconSvg(name){
    var paths = {
      folder:'<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
      'folder-plus':'<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
      upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
      star:'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
      share:'<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>',
      trash:'<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
      user:'<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
      moon:'<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
      palette:'<circle cx="12" cy="12" r="10"/><circle cx="8" cy="8" r="1"/><circle cx="12" cy="6" r="1"/><circle cx="16" cy="8" r="1"/>',
      image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
      video:'<polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
      audio:'<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
      file:'<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/>'
    };
    return '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (paths[name]||paths.file) + '</svg>';
  }
  function esc(s){ var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML; }
  document.addEventListener('input', function(e){
    if(e.target && e.target.id === 'paletteInput'){
      clearTimeout(paletteTimer);
      var q = e.target.value;
      paletteTimer = setTimeout(function(){
        if(!q.trim()){ renderPalette('', null); return; }
        fetch(D.urls.search + '?q=' + encodeURIComponent(q), {credentials:'same-origin'})
          .then(function(r){ return r.ok ? r.json() : {files:[],folders:[]}; })
          .then(function(d){ renderPalette(q, d); })
          .catch(function(){ renderPalette(q, {files:[],folders:[]}); });
      }, 130);
    }
  });
  document.addEventListener('keydown', function(e){
    if((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k'){ e.preventDefault(); openPalette(); return; }
    if(!document.getElementById('paletteModal').classList.contains('show')) return;
    if(e.key === 'ArrowDown'){ e.preventDefault(); paletteIndex = Math.min(paletteItems.length-1, paletteIndex+1); highlight(); }
    if(e.key === 'ArrowUp'){ e.preventDefault(); paletteIndex = Math.max(0, paletteIndex-1); highlight(); }
    if(e.key === 'Enter'){
      var el = $$('.palette-item')[paletteIndex];
      if(el){ e.preventDefault(); el.click(); }
    }
  });

  /* ---------- preview ---------- */
  window.openPreview = function(id, name, kind){
    var body = document.getElementById('previewBody'); if(!body) return;
    document.getElementById('previewName').textContent = name || 'Preview';
    document.getElementById('previewDownload').href = '/download/' + id;
    body.innerHTML = '<div class="spin" style="color:var(--accent);width:26px;height:26px;border-width:3px"></div>';
    var m = document.getElementById('previewModal'); m.classList.add('show'); document.body.classList.add('no-scroll');
    var url = '/preview/' + id;
    if(kind === 'image'){
      body.innerHTML = '<img alt="preview" src="' + url + '">';
    } else if(kind === 'video'){
      body.innerHTML = '<video controls autoplay src="' + url + '"></video>';
    } else if(kind === 'audio'){
      body.innerHTML = '<div style="text-align:center;width:100%"><div style="font-size:46px;color:var(--accent);margin-bottom:14px">♪</div><audio controls autoplay src="' + url + '"></audio></div>';
    } else if(kind === 'pdf'){
      body.innerHTML = '<iframe src="' + url + '" title="PDF preview"></iframe>';
    } else if(kind === 'text'){
      fetch(url, {credentials:'same-origin'}).then(function(r){ return r.text(); }).then(function(t){
        body.innerHTML = '';
        var pre = document.createElement('pre'); pre.textContent = t; body.appendChild(pre);
      }).catch(function(){ body.innerHTML = '<p class="hint">Could not load preview.</p>'; });
    } else {
      body.innerHTML = '<div class="empty"><div class="empty-ico">' + iconSvg('file') + '</div><h4>No inline preview</h4><p>Download the file to open it with your device.</p><a class="btn mt-1" href="/download/' + id + '">Download file</a></div>';
    }
  };
  window.closePreview = function(){
    var m = document.getElementById('previewModal'); if(m) m.classList.remove('show');
    var body = document.getElementById('previewBody'); if(body) body.innerHTML = '';
    document.body.classList.remove('no-scroll');
  };

  /* ---------- selection / bulk ---------- */
  var selected = new Set();
  function updateBulk(){
    var bar = document.getElementById('bulkBar'); if(!bar) return;
    bar.classList.toggle('show', selected.size > 0);
    var c = document.getElementById('bulkCount'); if(c) c.textContent = selected.size;
  }
  window.toggleSelect = function(id, checked){
    id = String(id);
    if(checked) selected.add(id); else selected.delete(id);
    var card = document.querySelector('[data-file-card="' + id + '"]');
    if(card) card.classList.toggle('selected', selected.has(id));
    var cb = document.querySelector('[data-select="' + id + '"]'); if(cb) cb.checked = selected.has(id);
    updateBulk();
  };
  window.selectAll = function(checked){
    $$('[data-select]').forEach(function(cb){
      cb.checked = checked;
      var id = cb.dataset.select;
      if(checked) selected.add(id); else selected.delete(id);
      var card = document.querySelector('[data-file-card="' + id + '"]');
      if(card) card.classList.toggle('selected', checked);
    });
    updateBulk();
  };
  window.clearSelection = function(){
    selected.clear(); $$('[data-select]').forEach(function(cb){ cb.checked = false; });
    $$('[data-file-card]').forEach(function(c){ c.classList.remove('selected'); });
    updateBulk();
  };
  window.bulkAction = function(action){
    var ids = Array.from(selected);
    if(!ids.length){ toast('Select files first', 'warn'); return; }
    if(action === 'move'){ openMove(ids.join(',')); return; }
    if(action === 'delete' && !confirm('Move ' + ids.length + ' selected file(s) to trash?')) return;
    var f = document.createElement('form'); f.method = 'post'; f.action = '/bulk';
    var csrf = document.createElement('input'); csrf.type='hidden'; csrf.name='_csrf_token'; csrf.value=D.csrf; f.appendChild(csrf);
    var act = document.createElement('input'); act.type='hidden'; act.name='action'; act.value=action; f.appendChild(act);
    var idf = document.createElement('input'); idf.type='hidden'; idf.name='ids'; idf.value=ids.join(','); f.appendChild(idf);
    if(action === 'download') f.target = '_blank';
    document.body.appendChild(f); f.submit();
  };

  /* ---------- file operations ---------- */
  window.starFile = function(id, btn){
    fetch(D.urls.star.replace(/\/$/, '') + '/' + id + '/star', {method:'POST', credentials:'same-origin', headers:{'X-CSRF-Token':D.csrf}})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        if(!d) throw new Error('failed');
        if(btn) btn.classList.toggle('on', !!d.starred);
        toast(d.starred ? 'Added to starred' : 'Removed from starred', 'success');
      }).catch(function(){ toast('Could not update star', 'error'); });
  };
  window.renameFile = function(id, name){
    var next = prompt('Rename file', name);
    if(next === null) return;
    next = next.trim(); if(!next || next === name) return;
    postForm('/file/' + id + '/rename', {name:next});
  };
  window.openMove = function(ids){
    var form = document.getElementById('moveForm'); if(!form) return;
    form.action = '/bulk';
    document.getElementById('moveIds').value = ids;
    var sel = document.getElementById('moveFolder'); sel.innerHTML = '';
    (D.folders || []).forEach(function(f){
      var o = document.createElement('option');
      o.value = f.id; o.textContent = (f.depth ? '— '.repeat(f.depth) : '') + f.name;
      sel.appendChild(o);
    });
    openModal('moveModal');
  };
  window.openShare = function(id, name){
    var form = document.getElementById('shareForm');
    form.action = '/share/' + id;
    document.getElementById('shareTitle').textContent = 'Share “' + name + '”';
    openModal('shareModal');
  };
  window.openDetails = function(id){
    fetch('/api/file/' + id, {credentials:'same-origin'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        if(!d){ toast('Could not load details', 'error'); return; }
        var rows = [
          ['Name', d.name], ['Size', d.size], ['Type', d.mime || d.kind],
          ['Folder', d.folder || 'Home'], ['Uploaded', d.upload_date],
          ['Downloads', d.download_count], ['Share links', d.share_count]
        ];
        document.getElementById('detailsBody').innerHTML = '<div class="table-wrap"><table>' +
          rows.map(function(r){ return '<tr><td class="hint" style="width:120px">' + esc(r[0]) + '</td><td><b>' + esc(String(r[1] == null ? '—' : r[1])) + '</b></td></tr>'; }).join('') +
          '</table></div>';
        openModal('detailsModal');
      });
  };
  window.deleteFile = function(id, name){
    if(!confirm('Move “' + name + '” to trash?')) return;
    postForm('/delete/' + id, {});
  };
  window.renameFolder = function(id, name){
    var next = prompt('Rename folder', name);
    if(next === null) return;
    next = next.trim(); if(!next || next === name) return;
    postForm('/folder/' + id + '/rename', {name:next});
  };
  window.deleteFolder = function(id, name){
    if(!confirm('Delete folder “' + name + '”? Files inside will be moved to trash.')) return;
    postForm('/folder/' + id + '/delete', {});
  };
  window.newFolder = function(){
    var name = prompt('Folder name');
    if(name === null) return;
    name = name.trim(); if(!name) return;
    postForm('/folder/create', {name:name, parent_id: D.currentFolder || ''});
  };
  window.postForm = function(action, data){
    var f = document.createElement('form'); f.method = 'post'; f.action = action;
    var csrf = document.createElement('input'); csrf.type='hidden'; csrf.name='_csrf_token'; csrf.value=D.csrf; f.appendChild(csrf);
    Object.keys(data || {}).forEach(function(k){
      var i = document.createElement('input'); i.type='hidden'; i.name=k; i.value=data[k]; f.appendChild(i);
    });
    document.body.appendChild(f); f.submit();
  };

  /* ---------- context menu ---------- */
  function hideCtx(){ var c = document.getElementById('ctxMenu'); if(c) c.classList.remove('show'); }
  window.hideCtx = hideCtx;
  window.showCtx = function(e, id, name, kind){
    e.preventDefault(); e.stopPropagation();
    var m = document.getElementById('ctxMenu'); if(!m) return;
    var canP = ['image','video','audio','pdf','text'].indexOf(kind) !== -1;
    m.innerHTML = '';
    var items = [];
    if(canP) items.push(['eye','Preview',function(){ openPreview(id, name, kind); }]);
    items.push(['download','Download',function(){ location.href='/download/' + id; }]);
    items.push(['edit','Rename',function(){ renameFile(id, name); }]);
    items.push(['share','Share link',function(){ openShare(id, name); }]);
    items.push(['move','Move to…',function(){ openMove(String(id)); }]);
    items.push(['info','Details',function(){ openDetails(id); }]);
    items.push(['trash','Move to trash',function(){ deleteFile(id, name); }, true]);
    items.forEach(function(it){
      var b = document.createElement('button');
      if(it[3]) b.className = 'danger';
      b.innerHTML = iconSvg(it[0]) + '<span>' + esc(it[1]) + '</span>';
      b.onclick = function(){ hideCtx(); it[2](); };
      m.appendChild(b);
    });
    m.style.left = Math.min(e.clientX, window.innerWidth - 210) + 'px';
    m.style.top = Math.min(e.clientY, window.innerHeight - 250) + 'px';
    m.classList.add('show');
  };
  document.addEventListener('click', function(e){ if(!e.target.closest('#ctxMenu')) hideCtx(); });
  document.addEventListener('scroll', hideCtx, true);

  /* ---------- uploads ---------- */
  function initUpload(){
    var dz = document.getElementById('dropZone'); if(!dz) return;
    var fi = document.getElementById('fileInput');
    dz.addEventListener('click', function(){ fi.click(); });
    fi.addEventListener('change', function(){ if(fi.files.length) uploadFiles(fi.files); fi.value=''; });
    ['dragenter','dragover'].forEach(function(ev){ dz.addEventListener(ev, function(e){ e.preventDefault(); dz.classList.add('drag'); }); });
    ['dragleave','drop'].forEach(function(ev){ dz.addEventListener(ev, function(e){ e.preventDefault(); dz.classList.remove('drag'); }); });
    dz.addEventListener('drop', function(e){ if(e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });
    document.addEventListener('paste', function(e){
      if(!e.clipboardData || !e.clipboardData.files || !e.clipboardData.files.length) return;
      if(document.getElementById('dropZone')) uploadFiles(e.clipboardData.files);
    });
  }
  function uploadFiles(files){
    var fd = new FormData();
    for(var i=0;i<files.length;i++) fd.append('files', files[i]);
    fd.append('_csrf_token', D.csrf);
    fd.append('folder_id', D.currentFolder || '');
    var wrap = document.getElementById('uploadProgressWrap');
    var bar = document.getElementById('uploadProgress');
    var st = document.getElementById('uploadStatus');
    if(wrap) wrap.classList.add('show');
    if(bar) bar.style.width = '0%';
    if(st) st.textContent = 'Uploading ' + files.length + ' file(s)…';
    var xhr = new XMLHttpRequest();
    xhr.open('POST', D.urls.upload);
    xhr.upload.onprogress = function(e){
      if(e.lengthComputable && bar){
        var pct = Math.round(e.loaded / e.total * 100);
        bar.style.width = pct + '%';
        if(st) st.textContent = 'Uploading… ' + pct + '%';
      }
    };
    xhr.onload = function(){
      if(xhr.status >= 200 && xhr.status < 400){
        if(st) st.textContent = 'Upload complete. Refreshing…';
        if(bar) bar.style.width = '100%';
        toast(files.length + ' file(s) uploaded', 'success');
        setTimeout(function(){ location.href = location.pathname + (location.search || ''); }, 500);
      } else {
        if(st) st.textContent = 'Upload failed (' + xhr.status + ').';
        toast('Upload failed. Check file size and quota.', 'error');
      }
    };
    xhr.onerror = function(){ if(st) st.textContent = 'Network error.'; toast('Upload failed', 'error'); };
    xhr.send(fd);
  }

  /* ---------- view toggle / password ---------- */
  window.setView = function(view){
    var area = document.getElementById('fileArea'); if(!area) return;
    area.classList.toggle('file-grid', view === 'grid');
    area.classList.toggle('file-list', view === 'list');
    try{ localStorage.setItem('drz-view', view); }catch(e){}
    $$('.view-toggle button').forEach(function(b){ b.classList.toggle('active', b.dataset.view === view); });
  };
  window.togglePw = function(btn){
    var input = btn.parentElement.querySelector('input'); if(!input) return;
    input.type = input.type === 'password' ? 'text' : 'password';
  };

  /* ---------- init ---------- */
  document.addEventListener('DOMContentLoaded', function(){
    applyRelTimes();
    setInterval(applyRelTimes, 60000);
    initUpload();
    var view = 'grid';
    try{ view = localStorage.getItem('drz-view') || 'grid'; }catch(e){}
    if(document.getElementById('fileArea')) setView(view);
    var accent = 'indigo';
    try{ accent = localStorage.getItem('drz-accent') || 'indigo'; }catch(e){}
    $$('.accent-dot').forEach(function(el){ el.classList.toggle('active', el.dataset.accent === accent); });
    $$('.flash').forEach(function(el){ setTimeout(function(){ el.style.transition='opacity .3s'; el.style.opacity='0'; setTimeout(function(){ el.remove(); }, 320); }, 6500); });
    if('serviceWorker' in navigator && location.protocol === 'https:'){ navigator.serviceWorker.register('/sw.js').catch(function(){}); }
  });

  /* keyboard shortcuts */
  document.addEventListener('keydown', function(e){
    if(e.target && /input|textarea|select/i.test(e.target.tagName)) return;
    if(e.key === '/'){ e.preventDefault(); openPalette(); }
    if(e.key.toLowerCase() === 'u' && document.getElementById('fileInput')){ document.getElementById('fileInput').click(); }
    if(e.key.toLowerCase() === 'n' && document.getElementById('fileArea')){ newFolder(); }
  });
})();
'''

# ============================================================
# Rendering helpers
# ============================================================
CSRF = '<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">'


def render(body, page_title='Files', **ctx):
    ctx['page_title'] = page_title
    html = (BASE.replace('%%CSS%%', CSS)
                .replace('%%JS%%', JS)
                .replace('%%BODY%%', body))
    return render_template_string(html, **ctx)


@app.context_processor
def inject_globals():
    ctx = {
        'app_name': app_name(), 'theme': 'system', 'accent': 'indigo',
        'trash_count': 0, 'share_count': 0, 'storage_used': 0, 'storage_quota': 0,
        'storage_pct': 0, 'file_count': 0, 'all_folders': [], 'current_folder': 0,
    }
    if getattr(g, 'user', None):
        try:
            uid = g.user['id']
            ctx['theme'] = g.user['theme'] or 'system'
            ctx['accent'] = g.user['accent'] or 'indigo'
            ctx['storage_used'] = used_bytes(uid)
            ctx['storage_quota'] = quota_bytes(g.user)
            ctx['storage_pct'] = quota_pct(g.user)
            ctx['file_count'] = get_db().execute("SELECT COUNT(*) c FROM files WHERE user_id=?", (uid,)).fetchone()['c']
            ctx['trash_count'] = get_db().execute("SELECT COUNT(*) c FROM trash WHERE user_id=?", (uid,)).fetchone()['c']
            ctx['share_count'] = get_db().execute("SELECT COUNT(*) c FROM share_links WHERE created_by=?", (uid,)).fetchone()['c']
            ctx['all_folders'] = all_folders_flat(uid)
        except Exception:
            pass
    return ctx


# ============================================================
# Page templates
# ============================================================
LANDING_BODY = r"""
<div class="landing">
  <nav class="landing-nav">
    <a class="landing-brand" href="{{ url_for('index') }}">
      <div class="brand-logo">D</div><div><b>{{ app_name }}</b><span>Ultra file manager</span></div>
    </a>
    <div class="landing-links">
      <a href="#features">Features</a><a href="#why">Why DRZ</a>
      <a class="btn sm" href="{{ url_for('login') }}">{{ icon('lock', 14) }} Sign in</a>
    </div>
  </nav>

  <section class="hero">
    <div class="hero-badge">{{ icon('sparkles', 13) }} Fast · Private · Beautiful</div>
    <h1>Your files,<br><span class="grad">beautifully organized.</span></h1>
    <p>A modern file manager with folders, instant previews, secure share links, trash recovery and a powerful admin dashboard. Built for people who care about their files.</p>
    <div class="hero-actions">
      <a class="btn" href="{{ url_for('login') }}">{{ icon('rocket', 16) }} Get started</a>
      <a class="btn ghost" href="#features">{{ icon('eye', 16) }} Explore features</a>
    </div>
    <div class="hero-stats">
      <div class="hero-stat"><b>{{ public_users }}</b><span>Members</span></div>
      <div class="hero-stat"><b>{{ public_files }}</b><span>Files stored</span></div>
      <div class="hero-stat"><b>{{ public_bytes|filesize }}</b><span>Managed</span></div>
    </div>
  </section>

  <section class="landing-section" id="features">
    <h2>Everything you need, nothing you don't</h2>
    <p class="lead">A complete workspace for your files — designed to feel fast, look sharp and stay out of your way.</p>
    <div class="feature-grid">
      <div class="feature"><div class="f-ico">{{ icon('folder-plus', 21) }}</div><h3>Folders &amp; organization</h3><p>Create nested folders, move files between them and keep everything tidy with breadcrumb navigation.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('eye', 21) }}</div><h3>Instant previews</h3><p>View images, play video and audio, read PDFs and inspect text or code without downloading a thing.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('share', 21) }}</div><h3>Secure share links</h3><p>Share with expiring links, optional passwords, download limits and a clean public preview page.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('trash', 21) }}</div><h3>Trash recovery</h3><p>Deleted files are kept safe. Restore them or purge permanently, with automatic retention cleanup.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('command', 21) }}</div><h3>Command palette</h3><p>Press Ctrl K to search files and folders, jump anywhere and run actions without touching the mouse.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('shield', 21) }}</div><h3>Private &amp; protected</h3><p>CSRF protection, hashed passwords, per-user storage isolation and a PIN-protected admin panel.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('activity', 21) }}</div><h3>Live analytics</h3><p>Storage charts, file-type breakdowns, an online monitor and a filterable audit trail for admins.</p></div>
      <div class="feature"><div class="f-ico">{{ icon('palette', 21) }}</div><h3>Your look</h3><p>Light and dark themes, six accent colors, grid or list layouts and a responsive mobile experience.</p></div>
    </div>
  </section>

  <section class="landing-section" id="why">
    <div class="showcase">
      <div>
        <h2>Built for everyday use.</h2>
        <p class="lead">No clutter, no nonsense. Just a fast workspace that makes storing and sharing files feel effortless.</p>
        <ul class="check-list">
          <li>{{ icon('check', 17) }} <span>Drag &amp; drop or paste uploads with live progress.</span></li>
          <li>{{ icon('check', 17) }} <span>Bulk select, move, download as ZIP and delete.</span></li>
          <li>{{ icon('check', 17) }} <span>Star important files and find them instantly.</span></li>
          <li>{{ icon('check', 17) }} <span>Password-protected share links with expiry and limits.</span></li>
          <li>{{ icon('check', 17) }} <span>Admin tools: users, quotas, audit logs and live monitor.</span></li>
        </ul>
      </div>
      <div class="showcase-visual">
        {{ icon('files', 92) }}
        <div class="float-card fc1">{{ icon('image', 15) }} holiday.jpg</div>
        <div class="float-card fc2">{{ icon('share', 15) }} link ready</div>
        <div class="float-card fc3">{{ icon('check-circle', 15) }} upload complete</div>
      </div>
    </div>
  </section>

  <div class="cta-band">
    <h2>Ready to organize your world?</h2>
    <p>Sign in with your account and start uploading. Accounts are created by an administrator.</p>
    <a class="btn" href="{{ url_for('login') }}">{{ icon('arrow-right', 16) }} Open your workspace</a>
  </div>

  <footer class="landing-footer">
    <span>{{ app_name }} — {{ footer_note }}</span><span class="spacer"></span>
    <span>Built with Flask · {{ public_files }} files managed</span>
  </footer>
</div>
"""

LOGIN_BODY = r"""
<div class="auth-wrap">
  <div class="auth-card">
    <div class="auth-brand"><div class="brand-logo">D</div><div><h1>{{ app_name }}</h1><span>Secure storage &amp; sharing</span></div></div>
    <h2>Welcome back</h2>
    <p class="sub">Sign in to access your workspace.</p>
    <form method="post">
      """ + CSRF + r"""
      <div class="field"><label>Username</label><input name="username" placeholder="your username" autofocus required autocomplete="username"></div>
      <div class="field"><label>Password</label><div class="pw-wrap"><input name="password" type="password" placeholder="••••••••" required autocomplete="current-password"><button class="pw-toggle" type="button" onclick="togglePw(this)" aria-label="Show password">{{ icon('eye', 15) }}</button></div></div>
      <button class="btn" type="submit">{{ icon('lock', 15) }} Sign in</button>
    </form>
    <div class="auth-links">
      {% if allow_registration %}<a href="{{ url_for('register') }}">Create an account</a> · {% endif %}
      <a href="{{ url_for('index') }}">Back to home</a>
    </div>
  </div>
</div>
"""

REGISTER_BODY = r"""
<div class="auth-wrap">
  <div class="auth-card">
    <div class="auth-brand"><div class="brand-logo">D</div><div><h1>{{ app_name }}</h1><span>Create your account</span></div></div>
    <h2>Get started</h2>
    <p class="sub">Registration is open. Pick a username and a strong password.</p>
    <form method="post">
      """ + CSRF + r"""
      <div class="field"><label>Username</label><input name="username" placeholder="letters, numbers, . _ -" required autocomplete="username" pattern="[A-Za-z0-9_.\-]{3,40}"></div>
      <div class="field"><label>Password</label><div class="pw-wrap"><input name="password" type="password" placeholder="At least 8 characters" required minlength="8" autocomplete="new-password"><button class="pw-toggle" type="button" onclick="togglePw(this)" aria-label="Show password">{{ icon('eye', 15) }}</button></div></div>
      <div class="field"><label>Confirm password</label><input name="password2" type="password" placeholder="Repeat password" required minlength="8" autocomplete="new-password"></div>
      <button class="btn" type="submit">{{ icon('user', 15) }} Create account</button>
    </form>
    <div class="auth-links"><a href="{{ url_for('login') }}">Already have an account? Sign in</a></div>
  </div>
</div>
"""

FILES_BODY = r"""
<div class="stat-grid">
  <div class="stat"><div class="stat-ico">{{ icon('files', 22) }}</div><div><div class="stat-val">{{ total_files }}</div><div class="stat-label">{{ 'Starred files' if page_mode == 'starred' else ('Recent files' if page_mode == 'recent' else 'Files in workspace') }}</div></div></div>
  <div class="stat"><div class="stat-ico sky">{{ icon('hard-drive', 22) }}</div><div><div class="stat-val">{{ used|filesize }}</div><div class="stat-label">Storage used</div></div></div>
  <div class="stat"><div class="stat-ico green">{{ icon('share', 22) }}</div><div><div class="stat-val">{{ shares_n }}</div><div class="stat-label">Share links</div></div></div>
  <div class="stat"><div class="stat-ico amber">{{ icon('trash', 22) }}</div><div><div class="stat-val">{{ trash_n }}</div><div class="stat-label">Items in trash</div></div></div>
</div>

{% if show_upload %}
<div class="card">
  <div class="card-head"><h3>{{ icon('cloud-upload', 17) }} Upload files</h3><div class="spacer"></div><span class="hint">Max {{ max_upload_mb }} MB per request</span></div>
  <div class="dropzone" id="dropZone">
    <div class="dz-ico">{{ icon('upload', 25) }}</div>
    <div class="dz-title">Drag &amp; drop files here</div>
    <div class="dz-sub">or <b>click to browse</b> · paste files · multiple uploads supported</div>
    <input type="file" id="fileInput" multiple style="display:none">
  </div>
  <div class="progress-wrap" id="uploadProgressWrap"><div class="progress-bar" id="uploadProgress"></div></div>
  <div id="uploadStatus" class="hint" style="margin-top:8px"></div>
</div>
{% endif %}

<div class="card">
  <div class="toolbar">
    <div class="breadcrumbs">
      <a href="{{ url_for('index') }}">{{ icon('home', 14) }} Home</a>
      {% for c in crumbs %}<span class="sep">/</span><a href="{{ url_for('index', folder=c['id']) }}">{{ c['name'] }}</a>{% endfor %}
      {% if page_mode == 'starred' %}<span class="sep">/</span><span class="current">Starred</span>{% endif %}
      {% if page_mode == 'recent' %}<span class="sep">/</span><span class="current">Recent</span>{% endif %}
    </div>
    <div class="spacer"></div>
    <form method="get">
      {% if folder_id %}<input type="hidden" name="folder" value="{{ folder_id }}">{% endif %}
      <div class="input-group">{{ icon('search', 15) }}<input type="text" name="q" placeholder="Search files…" value="{{ q }}" style="min-width:190px"></div>
      <select name="sort" onchange="this.form.submit()">
        <option value="newest" {% if sort=='newest' %}selected{% endif %}>Newest</option>
        <option value="oldest" {% if sort=='oldest' %}selected{% endif %}>Oldest</option>
        <option value="name" {% if sort=='name' %}selected{% endif %}>Name A–Z</option>
        <option value="name_desc" {% if sort=='name_desc' %}selected{% endif %}>Name Z–A</option>
        <option value="size_desc" {% if sort=='size_desc' %}selected{% endif %}>Largest</option>
        <option value="size_asc" {% if sort=='size_asc' %}selected{% endif %}>Smallest</option>
      </select>
      <button class="btn ghost sm" type="submit">{{ icon('filter', 13) }} Apply</button>
    </form>
    <div class="view-toggle" role="group" aria-label="View">
      <button type="button" data-view="grid" onclick="setView('grid')" title="Grid view">{{ icon('grid', 15) }}</button>
      <button type="button" data-view="list" onclick="setView('list')" title="List view">{{ icon('list', 15) }}</button>
    </div>
    {% if show_upload %}<button class="btn sm" type="button" onclick="newFolder()">{{ icon('folder-plus', 14) }} New folder</button>{% endif %}
  </div>

  {% if folders %}
  <div class="folder-grid">
    {% for d in folders %}
    <div class="folder-card">
      <a class="folder-ico" href="{{ url_for('index', folder=d['id']) }}">{{ icon('folder', 20) }}</a>
      <a class="grow" href="{{ url_for('index', folder=d['id']) }}"><b>{{ d['name'] }}</b><span>{{ d['count'] }} items · {{ d['size']|filesize }}</span></a>
      <div class="folder-actions">
        <button type="button" title="Rename" onclick="renameFolder({{ d['id'] }}, {{ d['name']|tojson }})">{{ icon('edit', 14) }}</button>
        <button type="button" title="Delete" onclick="deleteFolder({{ d['id'] }}, {{ d['name']|tojson }})">{{ icon('trash', 14) }}</button>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if files %}
  <div id="fileArea" class="file-grid">
    {% for f in files %}
    {% set kind = file_kind(f['original_name']) %}
    <div class="file-card" data-file-card="{{ f['id'] }}" oncontextmenu="showCtx(event, {{ f['id'] }}, {{ f['original_name']|tojson }}, '{{ kind }}')">
      <label class="file-check"><input type="checkbox" data-select="{{ f['id'] }}" onchange="toggleSelect({{ f['id'] }}, this.checked)" aria-label="Select {{ f['original_name'] }}"></label>
      <button class="star-btn {% if f['is_starred'] %}on{% endif %}" type="button" onclick="starFile({{ f['id'] }}, this)" title="Star">{{ icon('star', 14) }}</button>
      {% if kind == 'image' %}
        <img class="file-thumb" src="/preview/{{ f['id'] }}" alt="" loading="lazy" onclick="openPreview({{ f['id'] }}, {{ f['original_name']|tojson }}, 'image')">
      {% else %}
        <div class="thumb-fallback" onclick="openPreview({{ f['id'] }}, {{ f['original_name']|tojson }}, '{{ kind }}')">{{ file_icon(f['original_name'], 38) }}</div>
      {% endif %}
      <div class="file-name" title="{{ f['original_name'] }}">{{ f['original_name'] }}</div>
      <div class="file-meta"><span>{{ f['file_size']|filesize }}</span><span class="dot-sep"></span><span data-rel="{{ f['upload_date'] }}">{{ f['upload_date'][:10] }}</span>{% if f['is_starred'] %}<span class="badge badge-stars">starred</span>{% endif %}</div>
      <div class="file-actions">
        {% if can_preview(f['original_name']) %}<button class="btn ghost xs" type="button" onclick="openPreview({{ f['id'] }}, {{ f['original_name']|tojson }}, '{{ kind }}')">{{ icon('eye', 12) }} View</button>{% endif %}
        <a class="btn ghost xs" href="{{ url_for('download', file_id=f['id']) }}">{{ icon('download', 12) }} Save</a>
        <button class="btn ghost xs" type="button" onclick="openShare({{ f['id'] }}, {{ f['original_name']|tojson }})" title="Share">{{ icon('share', 12) }}</button>
        <button class="btn ghost xs" type="button" onclick="openDetails({{ f['id'] }})" title="Details">{{ icon('info', 12) }}</button>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">
    <div class="empty-ico">{{ icon('files', 32) }}</div>
    <h4>{% if q %}No files match “{{ q }}”{% else %}This space is empty{% endif %}</h4>
    <p>{% if q %}Try a different search term or clear the filter.{% else %}Upload your first file or create a folder to get organized.{% endif %}</p>
    {% if show_upload %}<button class="btn mt-1" type="button" onclick="document.getElementById('fileInput').click()">{{ icon('upload', 14) }} Upload files</button>{% endif %}
  </div>
  {% endif %}
</div>

<div class="bulk-bar" id="bulkBar">
  <span class="count"><span id="bulkCount">0</span> selected</span>
  <button class="btn soft xs" type="button" onclick="bulkAction('download')">{{ icon('download', 13) }} ZIP</button>
  <button class="btn soft xs" type="button" onclick="bulkAction('move')">{{ icon('move', 13) }} Move</button>
  <button class="btn soft xs" type="button" onclick="bulkAction('star')">{{ icon('star', 13) }} Star</button>
  <button class="btn danger xs" type="button" onclick="bulkAction('delete')">{{ icon('trash', 13) }} Delete</button>
  <button class="icon-btn small" type="button" onclick="clearSelection()" title="Clear selection">{{ icon('x', 14) }}</button>
</div>
"""

SHARES_BODY = r"""
<div class="stat-grid">
  <div class="stat"><div class="stat-ico">{{ icon('share', 22) }}</div><div><div class="stat-val">{{ links|length }}</div><div class="stat-label">Active share links</div></div></div>
  <div class="stat"><div class="stat-ico green">{{ icon('download', 22) }}</div><div><div class="stat-val">{{ total_downloads }}</div><div class="stat-label">Downloads</div></div></div>
  <div class="stat"><div class="stat-ico amber">{{ icon('lock', 22) }}</div><div><div class="stat-val">{{ protected }}</div><div class="stat-label">Password protected</div></div></div>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('share', 17) }} Share links</h3><div class="spacer"></div><span class="hint">Create links from any file card</span></div>
  {% if links %}
  <div class="table-wrap">
    <table>
      <thead><tr><th>File</th><th>Link</th><th>Expires</th><th>Downloads</th><th></th></tr></thead>
      <tbody>
      {% for l in links %}
      <tr>
        <td><div class="flex" style="gap:7px;flex-wrap:nowrap">{{ file_icon(l['original_name'], 18) }}<b style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ l['original_name'] }}</b></div>{% if l['note'] %}<div class="hint" style="font-size:10.5px">{{ l['note'] }}</div>{% endif %}</td>
        <td><code style="cursor:pointer" onclick="copyText('{{ l['url'] }}')" title="Click to copy">{{ l['url'][:46] }}…</code>{% if l['password_hash'] %} <span class="badge badge-warn">{{ icon('lock', 10) }} protected</span>{% endif %}</td>
        <td class="hint" style="white-space:nowrap">{{ l['expires_at'] }}</td>
        <td>{{ l['download_count'] }}{% if l['max_downloads'] %} / {{ l['max_downloads'] }}{% endif %}</td>
        <td style="white-space:nowrap">
          <a class="btn ghost xs" href="{{ l['url'] }}" target="_blank" rel="noopener">{{ icon('external-link', 12) }} Open</a>
          <form method="post" action="{{ url_for('revoke_share', link_id=l['id']) }}" onsubmit="return confirm('Revoke this link?')" style="display:inline">
            """ + CSRF + r"""
            <button class="btn danger xs" type="submit">{{ icon('trash', 12) }} Revoke</button>
          </form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty"><div class="empty-ico">{{ icon('link', 30) }}</div><h4>No share links yet</h4><p>Open a file’s share action to create an expiring, optional password-protected link.</p></div>
  {% endif %}
</div>
"""

TRASH_BODY = r"""
<div class="stat-grid">
  <div class="stat"><div class="stat-ico amber">{{ icon('trash', 22) }}</div><div><div class="stat-val">{{ items|length }}</div><div class="stat-label">Items in trash</div></div></div>
  <div class="stat"><div class="stat-ico">{{ icon('database', 22) }}</div><div><div class="stat-val">{{ trash_bytes|filesize }}</div><div class="stat-label">Recoverable space</div></div></div>
  <div class="stat"><div class="stat-ico sky">{{ icon('clock', 22) }}</div><div><div class="stat-val">{{ retention }}d</div><div class="stat-label">Auto-delete after</div></div></div>
</div>
<div class="card">
  <div class="card-head">
    <h3>{{ icon('trash', 17) }} Trash</h3><div class="spacer"></div>
    {% if items %}
    <form method="post" action="{{ url_for('trash_restore_all') }}" style="display:inline">""" + CSRF + r"""<button class="btn ghost sm" type="submit">{{ icon('restore', 13) }} Restore all</button></form>
    <form method="post" action="{{ url_for('trash_empty') }}" onsubmit="return confirm('Permanently delete everything in trash? This cannot be undone.')" style="display:inline">""" + CSRF + r"""<button class="btn danger sm" type="submit">{{ icon('trash', 13) }} Empty trash</button></form>
    {% endif %}
  </div>
  {% if items %}
  <div class="table-wrap">
    <table>
      <thead><tr><th></th><th>Name</th><th>Size</th><th>Deleted</th><th></th></tr></thead>
      <tbody>
      {% for t in items %}
      <tr>
        <td>{{ file_icon(t['original_name'], 20) }}</td>
        <td><b>{{ t['original_name'] }}</b>{% if t['folder_name'] %}<div class="hint" style="font-size:10.5px">from {{ t['folder_name'] }}</div>{% endif %}</td>
        <td>{{ t['file_size']|filesize }}</td>
        <td class="hint" style="white-space:nowrap">{{ t['deleted_at'] }}</td>
        <td style="white-space:nowrap">
          <form method="post" action="{{ url_for('restore', trash_id=t['id']) }}" style="display:inline">""" + CSRF + r"""<button class="btn soft xs" type="submit">{{ icon('restore', 12) }} Restore</button></form>
          <form method="post" action="{{ url_for('purge', trash_id=t['id']) }}" onsubmit="return confirm('Delete permanently?')" style="display:inline">""" + CSRF + r"""<button class="btn danger xs" type="submit">{{ icon('trash', 12) }}</button></form>
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty"><div class="empty-ico">{{ icon('check-circle', 30) }}</div><h4>Trash is empty</h4><p>Deleted files will appear here so you can restore them before they are purged.</p></div>
  {% endif %}
</div>
"""

ACCOUNT_BODY = r"""
<div class="stat-grid">
  <div class="stat"><div class="stat-ico">{{ icon('files', 22) }}</div><div><div class="stat-val">{{ file_count }}</div><div class="stat-label">Files</div></div></div>
  <div class="stat"><div class="stat-ico sky">{{ icon('hard-drive', 22) }}</div><div><div class="stat-val">{{ storage_used|filesize }}</div><div class="stat-label">Storage used</div></div></div>
  <div class="stat"><div class="stat-ico green">{{ icon('share', 22) }}</div><div><div class="stat-val">{{ share_count }}</div><div class="stat-label">Share links</div></div></div>
  <div class="stat"><div class="stat-ico amber">{{ icon('trash', 22) }}</div><div><div class="stat-val">{{ trash_count }}</div><div class="stat-label">In trash</div></div></div>
</div>
<div class="grid-2">
  <div class="card">
    <div class="card-head"><h3>{{ icon('user', 17) }} Profile</h3></div>
    <form method="post" class="stack">
      """ + CSRF + r"""
      <input type="hidden" name="form_type" value="profile">
      <div class="field"><label>Username</label><input value="{{ g.user['username'] }}" disabled></div>
      <div class="field"><label>Display name</label><input name="display_name" value="{{ g.user['display_name'] or '' }}" maxlength="60" placeholder="Your name"></div>
      <div class="form-grid">
        <div class="field"><label>Theme</label><select name="theme"><option value="system" {% if g.user['theme']=='system' %}selected{% endif %}>System</option><option value="light" {% if g.user['theme']=='light' %}selected{% endif %}>Light</option><option value="dark" {% if g.user['theme']=='dark' %}selected{% endif %}>Dark</option></select></div>
        <div class="field"><label>Accent</label><select name="accent">{% for a in ['indigo','violet','emerald','rose','amber','cyan'] %}<option value="{{ a }}" {% if g.user['accent']==a %}selected{% endif %}>{{ a|capitalize }}</option>{% endfor %}</select></div>
      </div>
      <div class="form-actions"><button class="btn" type="submit">{{ icon('check', 14) }} Save profile</button><a class="btn ghost" href="{{ url_for('account_export') }}">{{ icon('download', 14) }} Export data</a></div>
    </form>
  </div>
  <div class="card">
    <div class="card-head"><h3>{{ icon('lock', 17) }} Change password</h3></div>
    <form method="post" class="stack">
      """ + CSRF + r"""
      <input type="hidden" name="form_type" value="password">
      <div class="field"><label>Current password</label><input type="password" name="current" required autocomplete="current-password"></div>
      <div class="field"><label>New password</label><div class="pw-wrap"><input type="password" name="new" required minlength="8" autocomplete="new-password" placeholder="At least 8 characters"><button class="pw-toggle" type="button" onclick="togglePw(this)">{{ icon('eye', 15) }}</button></div></div>
      <div class="form-actions"><button class="btn" type="submit">{{ icon('key', 14) }} Update password</button></div>
    </form>
    <hr>
    <div class="hint">Member since {{ g.user['created_at'][:10] }} · Last seen {{ g.user['last_seen'] or '—' }}</div>
  </div>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('gauge', 17) }} Storage</h3><div class="spacer"></div><span class="hint">{{ storage_used|filesize }}{% if storage_quota %} of {{ storage_quota|filesize }}{% else %} · unlimited{% endif %}</span></div>
  <div class="ring-wrap">
    <svg class="ring" width="92" height="92" viewBox="0 0 42 42"><circle cx="21" cy="21" r="15.915" fill="transparent" stroke="var(--bg-2)" stroke-width="5"/><circle cx="21" cy="21" r="15.915" fill="transparent" stroke="var(--accent)" stroke-width="5" stroke-linecap="round" stroke-dasharray="{{ storage_pct }} {{ 100 - storage_pct }}" stroke-dashoffset="25"/></svg>
    <div><div style="font-size:22px;font-weight:800">{{ storage_pct }}%</div><div class="hint">of your quota used</div>{% if storage_quota %}<div class="hint">{{ (storage_quota - storage_used)|filesize }} remaining</div>{% endif %}</div>
  </div>
</div>
"""

ADMIN_BODY = r"""
<div class="tabs">
  <a class="active" href="{{ url_for('admin_home') }}">{{ icon('gauge', 14) }} Dashboard</a>
  <a href="{{ url_for('admin_logs') }}">{{ icon('list', 14) }} Audit log</a>
  <a href="{{ url_for('admin_monitor') }}">{{ icon('satellite', 14) }} Live monitor</a>
  <a href="{{ url_for('admin_settings') }}">{{ icon('settings', 14) }} Settings</a>
  <a href="{{ url_for('admin_change_pin') }}">{{ icon('lock', 14) }} PIN</a>
</div>
<div class="stat-grid">
  <div class="stat"><div class="stat-ico">{{ icon('users', 22) }}</div><div><div class="stat-val">{{ total_users }}</div><div class="stat-label">Users</div></div></div>
  <div class="stat"><div class="stat-ico green">{{ icon('activity', 22) }}</div><div><div class="stat-val">{{ online_n }}</div><div class="stat-label">Online now</div></div></div>
  <div class="stat"><div class="stat-ico sky">{{ icon('files', 22) }}</div><div><div class="stat-val">{{ total_files }}</div><div class="stat-label">Files</div></div></div>
  <div class="stat"><div class="stat-ico amber">{{ icon('hard-drive', 22) }}</div><div><div class="stat-val">{{ total_bytes|filesize }}</div><div class="stat-label">Storage</div></div></div>
  <div class="stat"><div class="stat-ico rose">{{ icon('share', 22) }}</div><div><div class="stat-val">{{ total_shares }}</div><div class="stat-label">Share links</div></div></div>
</div>
<div class="grid-2">
  <div class="card">
    <div class="card-head"><h3>{{ icon('activity', 17) }} Activity · last 7 days</h3></div>
    <div class="chart-bars">{% for a in activity %}<div class="bar-wrap"><div class="bar" data-count="{{ a.count }}" style="height:{{ (a.count / max_activity * 100)|round(1) }}%"></div><div class="bar-label">{{ a.day[5:] }}</div></div>{% endfor %}</div>
  </div>
  <div class="card">
    <div class="card-head"><h3>{{ icon('pie', 17) }} File types</h3></div>
    {% if donut_segments %}
    <div class="donut-wrap">
      <svg class="donut" viewBox="0 0 42 42"><circle cx="21" cy="21" r="15.915" fill="transparent" stroke="var(--bg-2)" stroke-width="6"/>{% set ns = namespace(offset=25) %}{% for seg in donut_segments %}<circle cx="21" cy="21" r="15.915" fill="transparent" stroke="{{ seg.color }}" stroke-width="6" stroke-dasharray="{{ seg.pct|round(2) }} {{ 100 - seg.pct|round(2) }}" stroke-dashoffset="{{ ns.offset }}"/>{% set ns.offset = ns.offset - seg.pct|round(2) %}{% endfor %}</svg>
      <div class="legend">{% for seg in donut_segments %}<div class="legend-item"><span class="legend-dot" style="background:{{ seg.color }}"></span><span class="grow"><b>{{ seg.ext }}</b></span><span class="pct">{{ seg.count }}</span></div>{% endfor %}</div>
    </div>
    {% else %}<div class="empty" style="padding:24px"><p>No files uploaded yet.</p></div>{% endif %}
  </div>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('plus', 17) }} Create user</h3></div>
  <form method="post" action="{{ url_for('admin_create_user') }}" class="flex">
    """ + CSRF + r"""
    <input name="username" placeholder="Username" required style="max-width:170px">
    <input name="display_name" placeholder="Display name" style="max-width:170px">
    <input name="password" type="password" placeholder="Password (8+)" required minlength="8" style="max-width:190px">
    <input name="quota_mb" type="number" min="0" placeholder="Quota MB (0=∞)" style="max-width:150px">
    <label class="flex" style="gap:6px"><input type="checkbox" name="is_admin"> admin</label>
    <button class="btn" type="submit">{{ icon('plus', 14) }} Create</button>
  </form>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('users', 17) }} All users</h3><div class="spacer"></div><span class="hint">{{ total_users }} accounts</span></div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>User</th><th>Role</th><th>Files</th><th>Storage</th><th>Last seen</th><th>Actions</th></tr></thead>
      <tbody>
      {% for u in users %}
      <tr>
        <td><div class="flex" style="gap:8px;flex-wrap:nowrap"><span class="avatar" style="width:28px;height:28px;font-size:11px">{{ (u['display_name'] or u['username'])[0]|upper }}</span><div><b>{{ u['username'] }}</b><div class="hint" style="font-size:10.5px">{{ u['display_name'] or '—' }} · #{{ u['id'] }}</div></div></div></td>
        <td>{% if u['is_admin'] %}<span class="badge badge-admin">admin</span>{% else %}<span class="badge badge-user">user</span>{% endif %}</td>
        <td>{{ u['nfiles'] }}</td>
        <td>{{ u['bytes']|filesize }}</td>
        <td class="hint" style="white-space:nowrap">{{ u['last_seen'] or '—' }}</td>
        <td>
          <form method="post" action="{{ url_for('admin_update_user', user_id=u['id']) }}" class="flex" style="gap:4px;flex-wrap:nowrap">
            """ + CSRF + r"""
            <input name="display_name" value="{{ u['display_name'] or '' }}" placeholder="Name" style="width:100px;padding:5px 7px;font-size:11px">
            <input name="quota_mb" type="number" min="0" value="{{ (u['quota_bytes'] or 0)//1048576 }}" title="Quota MB" style="width:70px;padding:5px 7px;font-size:11px">
            <label title="Admin" style="display:flex;align-items:center"><input type="checkbox" name="is_admin" {% if u['is_admin'] %}checked{% endif %} {% if u['id']==g.user['id'] %}disabled{% endif %}></label>
            <button class="btn soft xs" type="submit">{{ icon('check', 11) }}</button>
          </form>
          <form method="post" action="{{ url_for('admin_reset_password', user_id=u['id']) }}" class="flex" style="gap:4px;flex-wrap:nowrap;margin-top:4px">
            """ + CSRF + r"""
            <input name="password" type="password" placeholder="New password" minlength="8" required style="width:110px;padding:5px 7px;font-size:11px">
            <button class="btn ghost xs" type="submit">{{ icon('key', 11) }}</button>
          </form>
          {% if u['id'] != g.user['id'] and u['username'] != 'admin' %}
          <form method="post" action="{{ url_for('admin_delete_user', user_id=u['id']) }}" onsubmit="return confirm('Delete {{ u['username'] }} and all their files?')" style="display:inline">""" + CSRF + r"""<button class="btn danger xs" type="submit">{{ icon('trash', 11) }}</button></form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('bell', 17) }} Recent activity</h3><div class="spacer"></div><a class="btn ghost sm" href="{{ url_for('admin_logs') }}">{{ icon('list', 13) }} Full log</a><a class="btn sm" href="{{ url_for('admin_monitor') }}">{{ icon('satellite', 13) }} Live</a></div>
  <div class="table-wrap">
    <table><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Status</th><th>Detail</th></tr></thead><tbody>
    {% for r in recent %}<tr><td class="hint" style="white-space:nowrap">{{ r['timestamp'] }}</td><td>{{ r['username'] }}</td><td><code>{{ r['action'] }}</code></td><td>{% if r['status']=='ok' %}<span class="badge badge-ok">ok</span>{% else %}<span class="badge badge-fail">{{ r['status'] }}</span>{% endif %}</td><td class="hint">{{ r['failure_reason'] }}</td></tr>{% endfor %}
    </tbody></table>
  </div>
</div>
"""

LOGS_BODY = r"""
<div class="tabs">
  <a href="{{ url_for('admin_home') }}">{{ icon('gauge', 14) }} Dashboard</a>
  <a class="active" href="{{ url_for('admin_logs') }}">{{ icon('list', 14) }} Audit log</a>
  <a href="{{ url_for('admin_monitor') }}">{{ icon('satellite', 14) }} Live monitor</a>
  <a href="{{ url_for('admin_settings') }}">{{ icon('settings', 14) }} Settings</a>
</div>
<div class="stat-grid">
  <div class="stat"><div class="stat-ico">{{ icon('list', 22) }}</div><div><div class="stat-val">{{ total }}</div><div class="stat-label">Total events</div></div></div>
  <div class="stat"><div class="stat-ico rose">{{ icon('alert', 22) }}</div><div><div class="stat-val">{{ fails }}</div><div class="stat-label">Failed events</div></div></div>
  <div class="stat"><div class="stat-ico sky">{{ icon('search', 22) }}</div><div><div class="stat-val">{{ rows|length }}</div><div class="stat-label">Showing</div></div></div>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('list', 17) }} Audit log</h3><div class="spacer"></div><a class="btn ghost sm" href="{{ url_for('admin_logs_export', q=q, status=status, action=action) }}">{{ icon('csv', 13) }} Export CSV</a></div>
  <form method="get" class="toolbar">
    <div class="input-group">{{ icon('search', 15) }}<input type="text" name="q" placeholder="Search user, IP, action…" value="{{ q }}"></div>
    <select name="status"><option value="">All statuses</option><option value="ok" {% if status=='ok' %}selected{% endif %}>OK</option><option value="fail" {% if status=='fail' %}selected{% endif %}>Failed</option></select>
    <select name="action"><option value="">All actions</option>{% for a in actions %}<option value="{{ a }}" {% if action==a %}selected{% endif %}>{{ a }}</option>{% endfor %}</select>
    <button class="btn sm" type="submit">{{ icon('filter', 13) }} Apply</button>
    <a class="btn ghost sm" href="{{ url_for('admin_logs') }}">Reset</a>
  </form>
  <div class="table-wrap">
    <table><thead><tr><th>Time (UTC)</th><th>User</th><th>IP</th><th>Action</th><th>Status</th><th>Detail</th><th>User-Agent</th></tr></thead><tbody>
    {% for r in rows %}<tr><td class="hint" style="white-space:nowrap">{{ r['timestamp'] }}</td><td>{{ r['username'] }}</td><td class="hint">{{ r['ip_address'] }}</td><td><code>{{ r['action'] }}</code></td><td>{% if r['status']=='ok' %}<span class="badge badge-ok">ok</span>{% else %}<span class="badge badge-fail">{{ r['status'] }}</span>{% endif %}</td><td class="hint">{{ r['failure_reason'] }}</td><td class="hint" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ r['user_agent'] }}</td></tr>{% endfor %}
    </tbody></table>
  </div>
  <div class="pagination">
    {% if page > 1 %}<a href="{{ url_for('admin_logs', page=page-1, q=q, status=status, action=action) }}">← Prev</a>{% endif %}
    <span class="current">{{ page }} / {{ pages }}</span>
    {% if page < pages %}<a href="{{ url_for('admin_logs', page=page+1, q=q, status=status, action=action) }}">Next →</a>{% endif %}
  </div>
</div>
"""

MONITOR_BODY = r"""
<div class="tabs">
  <a href="{{ url_for('admin_home') }}">{{ icon('gauge', 14) }} Dashboard</a>
  <a href="{{ url_for('admin_logs') }}">{{ icon('list', 14) }} Audit log</a>
  <a class="active" href="{{ url_for('admin_monitor') }}">{{ icon('satellite', 14) }} Live monitor</a>
  <a href="{{ url_for('admin_settings') }}">{{ icon('settings', 14) }} Settings</a>
</div>
<div class="card">
  <div class="card-head">
    <h3>{{ icon('satellite', 17) }} Eye in the sky <span class="hint" style="font-weight:500">auto-refresh · 5s</span></h3>
    <div class="spacer"></div>
    <label class="flex" style="gap:6px;font-size:12px"><input type="checkbox" id="pauseToggle"> Pause</label>
    <input type="text" id="filterInput" placeholder="Filter activity…" style="width:200px">
  </div>
  <div id="users" style="display:flex;flex-direction:column;gap:7px"></div>
</div>
<div class="card">
  <div class="card-head"><h3>{{ icon('activity', 17) }} Recent activity</h3></div>
  <div class="table-wrap">
    <table><thead><tr><th>Time</th><th>User</th><th>IP</th><th>Action</th><th>Status</th><th>Detail</th></tr></thead><tbody id="log"></tbody></table>
  </div>
</div>
<script>
(function(){
  var paused=false, filterText='', logData=[];
  var userBox=document.getElementById('users'), logBox=document.getElementById('log');
  document.getElementById('pauseToggle').addEventListener('change',function(e){paused=e.target.checked;});
  document.getElementById('filterInput').addEventListener('input',function(e){filterText=e.target.value.toLowerCase();render();});
  function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
  function render(){
    logBox.replaceChildren();
    logData.forEach(function(x){
      var hay=(x.timestamp+' '+x.username+' '+x.ip_address+' '+x.action+' '+x.status+' '+(x.failure_reason||'')).toLowerCase();
      if(filterText && hay.indexOf(filterText)===-1) return;
      var tr=document.createElement('tr');
      var td=document.createElement('td'); td.className='hint'; td.textContent=relTime(x.timestamp); td.title=x.timestamp; tr.appendChild(td);
      [x.username,x.ip_address].forEach(function(v){var c=document.createElement('td');c.textContent=v;tr.appendChild(c);});
      var a=document.createElement('td'); var code=document.createElement('code'); code.textContent=x.action; a.appendChild(code); tr.appendChild(a);
      var s=document.createElement('td'); var b=document.createElement('span'); b.className='badge '+(x.status==='ok'?'badge-ok':'badge-fail'); b.textContent=x.status; s.appendChild(b); tr.appendChild(s);
      var d=document.createElement('td'); d.className='hint'; d.textContent=x.failure_reason||''; tr.appendChild(d);
      logBox.appendChild(tr);
    });
  }
  async function tick(){
    if(paused) return;
    try{
      var r=await fetch('{{ url_for("admin_monitor_data") }}',{credentials:'same-origin'});
      if(r.status!==200){location.href='{{ url_for("admin_pin") }}';return;}
      var d=await r.json();
      userBox.replaceChildren();
      d.users.forEach(function(x){
        var row=document.createElement('div');
        row.style.cssText='display:flex;align-items:center;gap:10px;padding:9px 12px;background:var(--surface-2);border-radius:9px';
        var dot=document.createElement('span'); dot.className='dot'+(x.online?' on':''); row.appendChild(dot);
        var name=document.createElement('b'); name.textContent=x.username; row.appendChild(name);
        var meta=document.createElement('span'); meta.className='hint'; meta.style.marginLeft='auto'; meta.textContent=x.online?'online':('last seen '+relTime(x.last_seen)); row.appendChild(meta);
        userBox.appendChild(row);
      });
      logData=d.logs; render();
    }catch(e){}
  }
  tick(); setInterval(tick,5000);
})();
</script>
"""

SETTINGS_BODY = r"""
<div class="tabs">
  <a href="{{ url_for('admin_home') }}">{{ icon('gauge', 14) }} Dashboard</a>
  <a href="{{ url_for('admin_logs') }}">{{ icon('list', 14) }} Audit log</a>
  <a href="{{ url_for('admin_monitor') }}">{{ icon('satellite', 14) }} Live monitor</a>
  <a class="active" href="{{ url_for('admin_settings') }}">{{ icon('settings', 14) }} Settings</a>
</div>
<div class="grid-2">
  <div class="card">
    <div class="card-head"><h3>{{ icon('settings', 17) }} Application settings</h3></div>
    <form method="post" class="stack">
      """ + CSRF + r"""
      <div class="field"><label>Application name</label><input name="app_name" value="{{ s.app_name }}" maxlength="60" required></div>
      <div class="form-grid">
        <div class="field"><label>Max upload (MB)</label><input type="number" name="max_upload_mb" min="1" max="10240" value="{{ s.max_upload_mb }}"></div>
        <div class="field"><label>Default quota (MB, 0 = ∞)</label><input type="number" name="default_quota_mb" min="0" value="{{ s.default_quota_mb }}"></div>
        <div class="field"><label>Trash retention (days)</label><input type="number" name="trash_retention_days" min="0" max="3650" value="{{ s.trash_retention_days }}"></div>
        <div class="field"><label>Default share expiry (hours)</label><input type="number" name="share_default_hours" min="1" max="720" value="{{ s.share_default_hours }}"></div>
      </div>
      <div class="field"><label>Footer note</label><input name="footer_note" value="{{ s.footer_note }}" maxlength="120"></div>
      <label class="flex" style="gap:7px"><input type="checkbox" name="allow_registration" value="1" {% if s.allow_registration=='1' %}checked{% endif %}> Allow self-registration</label>
      <div class="form-actions"><button class="btn" type="submit">{{ icon('check', 14) }} Save settings</button></div>
    </form>
  </div>
  <div class="card">
    <div class="card-head"><h3>{{ icon('backup', 17) }} Maintenance</h3></div>
    <p class="hint">Download a metadata backup of users, files, folders and share links. File contents are not included.</p>
    <a class="btn ghost" href="{{ url_for('admin_backup') }}">{{ icon('download', 14) }} Download backup</a>
    <hr>
    <h3 style="font-size:13px;margin:0 0 8px">{{ icon('shield', 16) }} Security</h3>
    <p class="hint">The admin panel is protected by your account and a 6-digit PIN. Change the PIN regularly and never reuse passwords.</p>
    <a class="btn ghost" href="{{ url_for('admin_change_pin') }}">{{ icon('key', 14) }} Change admin PIN</a>
  </div>
</div>
"""

PIN_BODY = r"""
<div class="auth-wrap">
  <div class="auth-card" style="max-width:390px">
    <div class="auth-brand"><div class="brand-logo">{{ icon('shield', 24) }}</div><div><h1>Admin access</h1><span>Extra security layer</span></div></div>
    <h2>{{ 'Create admin PIN' if setup else 'Enter admin PIN' }}</h2>
    <p class="sub">{{ 'Set up a 6-digit PIN to protect the admin panel.' if setup else 'Unlock the admin panel for 10 minutes.' }}</p>
    <form method="post">
      """ + CSRF + r"""
      <div class="field"><label>6-digit PIN</label><input name="pin" type="password" inputmode="numeric" maxlength="6" pattern="\d{6}" placeholder="••••••" required autofocus></div>
      {% if setup %}<div class="field"><label>Repeat PIN</label><input name="pin2" type="password" inputmode="numeric" maxlength="6" pattern="\d{6}" placeholder="••••••" required></div>{% endif %}
      <button class="btn" type="submit">{{ icon('lock', 15) }} {{ 'Save PIN' if setup else 'Unlock panel' }}</button>
    </form>
    <div class="auth-links"><a href="{{ url_for('index') }}">Back to files</a></div>
  </div>
</div>
"""

SHARE_PUBLIC_BODY = r"""
<div class="share-page">
  <div class="share-card">
    <div class="share-head">
      <div class="f-ico">{{ file_icon(link['original_name'], 25) }}</div>
      <div class="grow" style="min-width:0"><h1>{{ link['original_name'] }}</h1><p>Shared by {{ link['username'] }} · {{ app_name }}</p></div>
      <span class="badge badge-info">{{ icon('share', 11) }} shared</span>
    </div>
    {% if expired %}
    <div class="share-body" style="text-align:center;padding:40px 24px">
      <div class="empty-ico" style="margin:0 auto 14px">{{ icon('clock', 30) }}</div>
      <h2 style="font-size:18px;margin:0 0 6px">This link has expired</h2>
      <p class="hint">Ask the owner to create a fresh share link.</p>
    </div>
    {% elif locked %}
    <div class="share-body">
      <div style="text-align:center;margin-bottom:18px"><div class="empty-ico" style="margin:0 auto 12px">{{ icon('lock', 28) }}</div><h2 style="font-size:17px;margin:0 0 5px">Password required</h2><p class="hint">Enter the password to view or download this file.</p></div>
      {% if error %}<div class="flash err" style="margin-bottom:12px">{{ icon('alert', 15) }} {{ error }}</div>{% endif %}
      <form method="post" class="stack">
        """ + CSRF + r"""
        <div class="field"><label>Password</label><div class="pw-wrap"><input type="password" name="password" required autofocus autocomplete="off"><button class="pw-toggle" type="button" onclick="togglePw(this)">{{ icon('eye', 15) }}</button></div></div>
        <button class="btn block" type="submit">{{ icon('unlock', 15) }} Unlock</button>
      </form>
    </div>
    {% else %}
    <div class="share-preview">
      {% if allow_preview and kind == 'image' %}<img src="{{ url_for('shared_raw', token=link['token']) }}" alt="{{ link['original_name'] }}">
      {% elif allow_preview and kind == 'video' %}<video controls src="{{ url_for('shared_raw', token=link['token']) }}"></video>
      {% elif allow_preview and kind == 'audio' %}<audio controls src="{{ url_for('shared_raw', token=link['token']) }}"></audio>
      {% elif allow_preview and kind == 'pdf' %}<iframe src="{{ url_for('shared_raw', token=link['token']) }}"></iframe>
      {% elif allow_preview and kind == 'text' %}<iframe src="{{ url_for('shared_raw', token=link['token']) }}"></iframe>
      {% else %}<div class="empty" style="padding:26px"><div class="empty-ico">{{ file_icon(link['original_name'], 30) }}</div><p>Preview is not available. Download the file to open it.</p></div>{% endif %}
    </div>
    <div class="share-body">
      <div class="share-meta">
        <div class="item"><span>Size</span><b>{{ link['file_size']|filesize }}</b></div>
        <div class="item"><span>Expires</span><b>{{ link['expires_at'] or 'Never' }}</b></div>
        <div class="item"><span>Downloads</span><b>{{ link['download_count'] }}{% if link['max_downloads'] %} / {{ link['max_downloads'] }}{% endif %}</b></div>
        <div class="item"><span>Preview</span><b>{{ 'Enabled' if allow_preview else 'Disabled' }}</b></div>
      </div>
      {% if link['note'] %}<p class="hint" style="margin:0 0 14px">{{ icon('info', 14) }} {{ link['note'] }}</p>{% endif %}
      <a class="btn block" href="{{ url_for('shared_download', token=link['token']) }}">{{ icon('download', 16) }} Download file</a>
      <p class="hint" style="text-align:center;margin:12px 0 0">Powered by {{ app_name }}</p>
    </div>
    {% endif %}
  </div>
</div>
"""

# ============================================================
# Public pages / auth
# ============================================================
@app.route('/')
def index():
    if not g.user:
        try:
            db = get_db()
            pu = db.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
            pf = db.execute("SELECT COUNT(*) c FROM files").fetchone()['c']
            pb = db.execute("SELECT COALESCE(SUM(file_size),0) s FROM files").fetchone()['s']
        except Exception:
            pu, pf, pb = 0, 0, 0
        return render(LANDING_BODY, page_title=app_name(), landing=True,
                      public_users=pu, public_files=pf, public_bytes=pb,
                      footer_note=get_setting('footer_note', 'Private by design.'))
    return _files_page('Files')


def _files_page(title, starred=False, recent=False):
    db = get_db()
    uid = g.user['id']
    q = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'newest')
    folder_id = request.args.get('folder', type=int)
    if folder_id:
        get_folder_or_404(folder_id)
    order = {
        'newest': 'upload_date DESC, id DESC', 'oldest': 'upload_date ASC, id ASC',
        'name': 'original_name COLLATE NOCASE ASC', 'name_desc': 'original_name COLLATE NOCASE DESC',
        'size_desc': 'file_size DESC', 'size_asc': 'file_size ASC',
    }.get(sort, 'upload_date DESC, id DESC')
    where = ['user_id=?']
    params = [uid]
    if starred:
        where.append('is_starred=1')
    if folder_id:
        where.append('folder_id=?')
        params.append(folder_id)
    if q:
        where.append('original_name LIKE ?')
        params.append(f'%{q}%')
    sql = f"SELECT * FROM files WHERE {' AND '.join(where)} ORDER BY {order}"
    if recent:
        sql += ' LIMIT 24'
    files = db.execute(sql, params).fetchall()
    folders = []
    if not starred and not recent and not q:
        for d in folder_children(folder_id, uid):
            folders.append({'id': d['id'], 'name': d['name'],
                            'count': folder_file_count(d['id'], uid),
                            'size': folder_size(d['id'], uid)})
    crumbs = folder_breadcrumbs(folder_id) if folder_id else []
    total_files = db.execute("SELECT COUNT(*) c FROM files WHERE user_id=?", (uid,)).fetchone()['c']
    used = used_bytes(uid)
    shares_n = db.execute("SELECT COUNT(*) c FROM share_links WHERE created_by=?", (uid,)).fetchone()['c']
    trash_n = db.execute("SELECT COUNT(*) c FROM trash WHERE user_id=?", (uid,)).fetchone()['c']
    mode = 'starred' if starred else ('recent' if recent else 'files')
    return render(FILES_BODY, page_title=title, files=files, folders=folders, crumbs=crumbs,
                  folder_id=folder_id, q=q, sort=sort, show_upload=(mode == 'files' and not q),
                  shares_n=shares_n, trash_n=trash_n, total_files=total_files, used=used,
                  storage_pct=quota_pct(g.user), page_mode=mode,
                  max_upload_mb=setting_int('max_upload_mb', 2048),
                  current_folder=folder_id or 0)


@app.route('/starred')
@login_required
def starred():
    return _files_page('Starred', starred=True)


@app.route('/recent')
@login_required
def recent():
    return _files_page('Recent', recent=True)


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
            return render(LOGIN_BODY, page_title='Sign in', allow_registration=(get_setting('allow_registration') == '1')), 429
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user['password_hash'], password):
            _login_attempts.pop(key, None)
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            session['last_active'] = time.time()
            g.user = user
            audit('login')
            return redirect(safe_next(request.args.get('next')))
        _record_failure(_login_attempts, key)
        audit('login', 'fail', 'bad credentials', username)
        flash("Invalid username or password.", "err")
    return render(LOGIN_BODY, page_title='Sign in', allow_registration=(get_setting('allow_registration') == '1'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if get_setting('allow_registration') != '1':
        flash("Self-registration is disabled. Ask an administrator for an account.", "warn")
        return redirect(url_for('login'))
    if g.user:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        pw = request.form.get('password', '')
        pw2 = request.form.get('password2', '')
        if not valid_username(username):
            flash("Username must be 3–40 characters: letters, numbers, . _ -", "err")
        elif not valid_new_password(pw):
            flash("Password must be at least 8 characters.", "err")
        elif pw != pw2:
            flash("Passwords do not match.", "err")
        else:
            try:
                db = get_db()
                cur = db.execute("INSERT INTO users (username, display_name, password_hash) VALUES (?,?,?)",
                                 (username, username, generate_password_hash(pw)))
                db.commit()
                session.clear()
                session.permanent = True
                session['user_id'] = cur.lastrowid
                session['last_active'] = time.time()
                g.user = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
                audit('register')
                flash("Welcome! Your account is ready.", "ok")
                return redirect(url_for('index'))
            except sqlite3.IntegrityError:
                flash("That username already exists.", "err")
    return render(REGISTER_BODY, page_title='Create account')


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    audit('logout')
    session.clear()
    return redirect(url_for('login'))


# ============================================================
# Files & folders
# ============================================================
@app.route('/upload', methods=['POST'])
@login_required
def upload():
    folder_id = request.form.get('folder_id', type=int)
    if folder_id:
        get_folder_or_404(folder_id)
    folder = get_user_dir(g.user['id'])
    quota = quota_bytes(g.user)
    used = used_bytes(g.user['id'])
    saved = 0
    skipped = 0
    for f in request.files.getlist('files'):
        orig = clean_filename(f.filename or '')
        if not orig:
            skipped += 1
            continue
        stored = stored_name_for(orig)
        path = os.path.join(folder, stored)
        try:
            f.save(path)
            size = os.path.getsize(path)
        except OSError:
            skipped += 1
            continue
        if quota and used + size > quota:
            try:
                os.remove(path)
            except OSError:
                pass
            flash("Storage quota exceeded. Some files were not uploaded.", "err")
            break
        used += size
        mime = f.mimetype or mimetypes.guess_type(orig)[0] or ''
        get_db().execute("INSERT INTO files (user_id, folder_id, filename, original_name, file_size, mime, upload_date, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?)",
                         (g.user['id'], folder_id, stored, orig, size, mime, now_str(), now_str()))
        saved += 1
    get_db().commit()
    audit('upload', 'ok' if saved else 'fail', f'{saved} file(s), {skipped} skipped')
    flash(f"Uploaded {saved} file(s)." if saved else "No valid files selected.", "ok" if saved else "warn")
    if folder_id:
        return redirect(url_for('index', folder=folder_id))
    return redirect(url_for('index'))


@app.route('/download/<int:file_id>')
@login_required
def download(file_id):
    f = own_file_or_404(file_id)
    path = physical_path(f)
    if not os.path.isfile(path):
        abort(404)
    get_db().execute("UPDATE files SET download_count=download_count+1 WHERE id=?", (file_id,))
    get_db().commit()
    audit('download', 'ok', f['original_name'])
    return send_file(path, as_attachment=True, download_name=f['original_name'])


@app.route('/preview/<int:file_id>')
@login_required
def preview(file_id):
    f = own_file_or_404(file_id)
    path = physical_path(f)
    if not os.path.isfile(path):
        abort(404)
    mime = f['mime'] or mimetypes.guess_type(f['original_name'])[0] or 'application/octet-stream'
    if is_text(f['original_name']):
        mime = 'text/plain; charset=utf-8'
    resp = send_file(path, mimetype=mime, as_attachment=False, download_name=f['original_name'])
    resp.headers['Cache-Control'] = 'private, max-age=60'
    return resp


@app.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    f = own_file_or_404(file_id)
    move_file_to_trash(f)
    audit('trash', 'ok', f['original_name'])
    flash("Moved to trash.", "ok")
    return redirect(request.referrer or url_for('index'))


@app.route('/file/<int:file_id>/rename', methods=['POST'])
@login_required
def rename_file(file_id):
    f = own_file_or_404(file_id)
    name = clean_filename(request.form.get('name', ''))
    if not name:
        flash("Invalid file name.", "err")
    else:
        get_db().execute("UPDATE files SET original_name=?, updated_at=? WHERE id=?", (name, now_str(), file_id))
        get_db().commit()
        audit('rename', 'ok', f'{f["original_name"]} -> {name}')
        flash("File renamed.", "ok")
    return redirect(request.referrer or url_for('index'))


@app.route('/file/<int:file_id>/star', methods=['POST'])
@login_required
def star_file(file_id):
    f = own_file_or_404(file_id)
    new = 0 if f['is_starred'] else 1
    get_db().execute("UPDATE files SET is_starred=? WHERE id=?", (new, file_id))
    get_db().commit()
    return jsonify(starred=bool(new))


@app.route('/file/<int:file_id>/copy', methods=['POST'])
@login_required
def copy_file(file_id):
    f = own_file_or_404(file_id)
    src = physical_path(f)
    if not os.path.isfile(src):
        abort(404)
    stored = stored_name_for(f['original_name'])
    dst = os.path.join(get_user_dir(g.user['id']), stored)
    shutil.copy2(src, dst)
    db = get_db()
    db.execute("INSERT INTO files (user_id, folder_id, filename, original_name, file_size, mime, upload_date, updated_at) "
               "VALUES (?,?,?,?,?,?,?,?)",
               (g.user['id'], f['folder_id'], stored, 'Copy of ' + f['original_name'],
                os.path.getsize(dst), f['mime'], now_str(), now_str()))
    db.commit()
    audit('copy', 'ok', f['original_name'])
    flash("File duplicated.", "ok")
    return redirect(request.referrer or url_for('index'))


@app.route('/bulk', methods=['POST'])
@login_required
def bulk():
    action = request.form.get('action', '')
    raw = request.form.get('ids', '')
    ids = [int(x) for x in raw.split(',') if x.strip().isdigit()]
    if not ids:
        flash("No files selected.", "warn")
        return redirect(url_for('index'))
    marks = ','.join('?' * len(ids))
    db = get_db()
    rows = db.execute(f"SELECT * FROM files WHERE user_id=? AND id IN ({marks})", (g.user['id'], *ids)).fetchall()
    if not rows:
        flash("No files found.", "warn")
        return redirect(url_for('index'))
    if action == 'delete':
        for f in rows:
            move_file_to_trash(f)
        flash(f"Moved {len(rows)} file(s) to trash.", "ok")
        audit('bulk_delete', 'ok', f'{len(rows)} file(s)')
    elif action == 'star':
        db.execute(f"UPDATE files SET is_starred=1 WHERE user_id=? AND id IN ({marks})", (g.user['id'], *ids))
        db.commit()
        flash(f"Starred {len(rows)} file(s).", "ok")
    elif action == 'unstar':
        db.execute(f"UPDATE files SET is_starred=0 WHERE user_id=? AND id IN ({marks})", (g.user['id'], *ids))
        db.commit()
        flash(f"Unstarred {len(rows)} file(s).", "ok")
    elif action == 'move':
        folder_id = request.form.get('folder_id', type=int) or None
        if folder_id:
            get_folder_or_404(folder_id)
        db.execute(f"UPDATE files SET folder_id=? WHERE user_id=? AND id IN ({marks})", (folder_id, g.user['id'], *ids))
        db.commit()
        flash(f"Moved {len(rows)} file(s).", "ok")
        audit('bulk_move', 'ok', f'{len(rows)} file(s)')
    elif action == 'download':
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            used_names = set()
            for f in rows:
                p = physical_path(f)
                if not os.path.isfile(p):
                    continue
                name = f['original_name'] or f['filename']
                base, dot, ext = name.rpartition('.')
                candidate = name
                n = 2
                while candidate in used_names:
                    candidate = f"{base} ({n}).{ext}" if dot else f"{name} ({n})"
                    n += 1
                used_names.add(candidate)
                zf.write(p, candidate)
        buf.seek(0)
        audit('bulk_download', 'ok', f'{len(rows)} file(s)')
        return send_file(buf, mimetype='application/zip', as_attachment=True, download_name='drz-files.zip')
    else:
        flash("Unknown action.", "err")
    return redirect(request.referrer or url_for('index'))


@app.route('/folder/create', methods=['POST'])
@login_required
def folder_create():
    name = clean_filename(request.form.get('name', ''))[:80]
    parent_id = request.form.get('parent_id', type=int) or None
    if parent_id:
        get_folder_or_404(parent_id)
    if not name:
        flash("Folder name is required.", "err")
    else:
        db = get_db()
        exists = db.execute("SELECT id FROM folders WHERE user_id=? AND parent_id IS ? AND name=?",
                            (g.user['id'], parent_id, name)).fetchone()
        if exists:
            flash("A folder with that name already exists here.", "warn")
        else:
            db.execute("INSERT INTO folders (user_id, name, parent_id) VALUES (?,?,?)", (g.user['id'], name, parent_id))
            db.commit()
            audit('folder_create', 'ok', name)
            flash("Folder created.", "ok")
    if parent_id:
        return redirect(url_for('index', folder=parent_id))
    return redirect(url_for('index'))


@app.route('/folder/<int:folder_id>/rename', methods=['POST'])
@login_required
def folder_rename(folder_id):
    f = get_folder_or_404(folder_id)
    name = clean_filename(request.form.get('name', ''))[:80]
    if not name:
        flash("Folder name is required.", "err")
    else:
        get_db().execute("UPDATE folders SET name=? WHERE id=?", (name, folder_id))
        get_db().commit()
        audit('folder_rename', 'ok', f['name'])
        flash("Folder renamed.", "ok")
    return redirect(request.referrer or url_for('index'))


@app.route('/folder/<int:folder_id>/delete', methods=['POST'])
@login_required
def folder_delete(folder_id):
    f = get_folder_or_404(folder_id)
    db = get_db()
    ids = folder_descendants(folder_id, g.user['id'])
    marks = ','.join('?' * len(ids))
    files = db.execute(f"SELECT * FROM files WHERE user_id=? AND folder_id IN ({marks})", (g.user['id'], *ids)).fetchall()
    for row in files:
        move_file_to_trash(row, f['name'])
    db.execute(f"DELETE FROM folders WHERE user_id=? AND id IN ({marks})", (g.user['id'], *ids))
    db.commit()
    audit('folder_delete', 'ok', f'{f["name"]} ({len(files)} files)')
    flash(f"Folder deleted. {len(files)} file(s) moved to trash.", "ok")
    return redirect(url_for('index'))


# ============================================================
# Trash
# ============================================================
@app.route('/trash')
@login_required
def trash_view():
    purge_expired_trash()
    items = get_db().execute("SELECT * FROM trash WHERE user_id=? ORDER BY deleted_at DESC", (g.user['id'],)).fetchall()
    trash_bytes = sum(t['file_size'] or 0 for t in items)
    return render(TRASH_BODY, page_title='Trash', items=items, trash_bytes=trash_bytes,
                  retention=setting_int('trash_retention_days', 30))


@app.route('/trash/restore/<int:trash_id>', methods=['POST'])
@login_required
def restore(trash_id):
    t = own_trash_or_404(trash_id)
    src = trash_physical_path(t)
    dst_name = t['stored_name'] or t['original_path']
    dst = os.path.join(get_user_dir(g.user['id']), dst_name)
    if os.path.exists(dst):
        dst_name = stored_name_for(t['original_name'] or 'file')
        dst = os.path.join(get_user_dir(g.user['id']), dst_name)
    if not os.path.isfile(src):
        flash("File data is missing; removing the entry.", "err")
    else:
        shutil.move(src, dst)
        get_db().execute("INSERT INTO files (user_id, folder_id, filename, original_name, file_size, mime, upload_date, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?)",
                         (g.user['id'], None, dst_name, t['original_name'], t['file_size'],
                          t['mime'] or mimetypes.guess_type(t['original_name'] or '')[0] or '', now_str(), now_str()))
        audit('restore', 'ok', t['original_name'])
        flash("Restored to Home.", "ok")
    get_db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
    get_db().commit()
    return redirect(url_for('trash_view'))


@app.route('/trash/purge/<int:trash_id>', methods=['POST'])
@login_required
def purge(trash_id):
    t = own_trash_or_404(trash_id)
    p = trash_physical_path(t)
    try:
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass
    get_db().execute("DELETE FROM trash WHERE id=?", (trash_id,))
    get_db().commit()
    audit('purge', 'ok', t['original_name'])
    flash("Deleted permanently.", "ok")
    return redirect(url_for('trash_view'))


@app.route('/trash/empty', methods=['POST'])
@login_required
def trash_empty():
    rows = get_db().execute("SELECT * FROM trash WHERE user_id=?", (g.user['id'],)).fetchall()
    for t in rows:
        try:
            p = trash_physical_path(t)
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    get_db().execute("DELETE FROM trash WHERE user_id=?", (g.user['id'],))
    get_db().commit()
    audit('trash_empty', 'ok', f'{len(rows)} item(s)')
    flash("Trash emptied.", "ok")
    return redirect(url_for('trash_view'))


@app.route('/trash/restore-all', methods=['POST'])
@login_required
def trash_restore_all():
    rows = get_db().execute("SELECT * FROM trash WHERE user_id=?", (g.user['id'],)).fetchall()
    restored = 0
    for t in rows:
        src = trash_physical_path(t)
        if not os.path.isfile(src):
            continue
        dst_name = t['stored_name'] or t['original_path']
        dst = os.path.join(get_user_dir(g.user['id']), dst_name)
        if os.path.exists(dst):
            dst_name = stored_name_for(t['original_name'] or 'file')
            dst = os.path.join(get_user_dir(g.user['id']), dst_name)
        shutil.move(src, dst)
        get_db().execute("INSERT INTO files (user_id, folder_id, filename, original_name, file_size, mime, upload_date, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?)",
                         (g.user['id'], None, dst_name, t['original_name'], t['file_size'],
                          t['mime'] or '', now_str(), now_str()))
        restored += 1
    get_db().execute("DELETE FROM trash WHERE user_id=?", (g.user['id'],))
    get_db().commit()
    audit('trash_restore_all', 'ok', f'{restored} item(s)')
    flash(f"Restored {restored} file(s) to Home.", "ok")
    return redirect(url_for('trash_view'))


# ============================================================
# Sharing
# ============================================================
@app.route('/shares')
@login_required
def shares_view():
    db = get_db()
    rows = db.execute("SELECT s.*, f.original_name, f.file_size FROM share_links s JOIN files f ON f.id=s.file_id "
                      "WHERE s.created_by=? ORDER BY s.id DESC", (g.user['id'],)).fetchall()
    links = []
    for r in rows:
        d = dict(r)
        d['url'] = url_for('shared', token=r['token'], _external=True)
        links.append(d)
    total_downloads = sum(r['download_count'] or 0 for r in rows)
    protected = sum(1 for r in rows if r['password_hash'])
    return render(SHARES_BODY, page_title='Share links', links=links,
                  total_downloads=total_downloads, protected=protected)


@app.route('/share/<int:file_id>', methods=['POST'])
@login_required
def share(file_id):
    f = own_file_or_404(file_id)
    try:
        hours = max(1, min(int(request.form.get('hours', setting_int('share_default_hours', 24))), 720))
    except (TypeError, ValueError):
        hours = 24
    try:
        max_dl = max(0, min(int(request.form.get('max_downloads', 0) or 0), 100000))
    except (TypeError, ValueError):
        max_dl = 0
    password = request.form.get('password', '')
    note = (request.form.get('note', '') or '')[:120]
    allow_preview = 1 if request.form.get('allow_preview') else 0
    token = secrets.token_urlsafe(24)
    exp = now_str(utcnow() + timedelta(hours=hours))
    get_db().execute("INSERT INTO share_links (token, file_id, created_by, expires_at, created_at, password_hash, "
                     "max_downloads, download_count, allow_preview, note) VALUES (?,?,?,?,?,?,?,0,?,?)",
                     (token, file_id, g.user['id'], exp, now_str(),
                      generate_password_hash(password) if password else '', max_dl, allow_preview, note))
    get_db().commit()
    audit('share_create', 'ok', f['original_name'])
    flash("Share link created. Copy it from Share links.", "ok")
    return redirect(url_for('shares_view'))


@app.route('/share/revoke/<int:link_id>', methods=['POST'])
@login_required
def revoke_share(link_id):
    get_db().execute("DELETE FROM share_links WHERE id=? AND created_by=?", (link_id, g.user['id']))
    get_db().commit()
    audit('share_revoke', 'ok', f'link_id={link_id}')
    flash("Share link revoked.", "ok")
    return redirect(url_for('shares_view'))


def _share_row(token):
    return get_db().execute(
        "SELECT s.*, f.filename, f.original_name, f.file_size, f.user_id AS owner_id, u.username "
        "FROM share_links s JOIN files f ON f.id=s.file_id JOIN users u ON u.id=s.created_by "
        "WHERE s.token=?", (token,)).fetchone()


def _share_state(row):
    if not row:
        return 'missing'
    if row['expires_at'] and row['expires_at'] < now_str():
        return 'expired'
    if row['max_downloads'] and row['download_count'] >= row['max_downloads']:
        return 'expired'
    return 'ok'


@app.route('/s/<token>', methods=['GET', 'POST'])
def shared(token):
    row = _share_row(token)
    state = _share_state(row)
    if state == 'missing':
        abort(404)
    if state == 'expired':
        return render(SHARE_PUBLIC_BODY, page_title='Expired link', link=row, expired=True,
                      locked=False, error='', allow_preview=False, kind='file',
                      footer_note=get_setting('footer_note', 'Private by design.')), 410
    locked = bool(row['password_hash']) and not session.get('share_ok_' + str(row['id']))
    error = ''
    if request.method == 'POST' and locked:
        if check_password_hash(row['password_hash'], request.form.get('password', '')):
            session['share_ok_' + str(row['id'])] = True
            locked = False
            audit('share_unlock', 'ok', row['original_name'], 'anonymous')
            return redirect(url_for('shared', token=token))
        error = 'Incorrect password. Try again.'
        audit('share_unlock', 'fail', 'bad share password', 'anonymous')
    return render(SHARE_PUBLIC_BODY, page_title=row['original_name'], link=row, expired=False,
                  locked=locked, error=error, allow_preview=bool(row['allow_preview']),
                  kind=file_kind(row['original_name']),
                  footer_note=get_setting('footer_note', 'Private by design.'))


@app.route('/s/<token>/download')
def shared_download(token):
    row = _share_row(token)
    state = _share_state(row)
    if state != 'ok':
        abort(404)
    if row['password_hash'] and not session.get('share_ok_' + str(row['id'])):
        return redirect(url_for('shared', token=token))
    path = os.path.join(get_user_dir(row['owner_id']), row['filename'])
    if not os.path.isfile(path):
        abort(404)
    get_db().execute("UPDATE share_links SET download_count=download_count+1 WHERE id=?", (row['id'],))
    get_db().commit()
    audit('share_download', 'ok', row['original_name'], 'anonymous')
    return send_file(path, as_attachment=True, download_name=row['original_name'])


@app.route('/s/<token>/raw')
def shared_raw(token):
    row = _share_row(token)
    state = _share_state(row)
    if state != 'ok' or not row['allow_preview']:
        abort(404)
    if row['password_hash'] and not session.get('share_ok_' + str(row['id'])):
        abort(403)
    path = os.path.join(get_user_dir(row['owner_id']), row['filename'])
    if not os.path.isfile(path):
        abort(404)
    mime = mimetypes.guess_type(row['original_name'])[0] or 'application/octet-stream'
    if is_text(row['original_name']):
        mime = 'text/plain; charset=utf-8'
    return send_file(path, mimetype=mime, as_attachment=False, download_name=row['original_name'])


# ============================================================
# Account
# ============================================================
@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    db = get_db()
    if request.method == 'POST':
        form_type = request.form.get('form_type', 'profile')
        if form_type == 'profile':
            display_name = (request.form.get('display_name', '') or '').strip()[:60]
            theme = request.form.get('theme', 'system')
            accent = request.form.get('accent', 'indigo')
            if theme not in ('system', 'light', 'dark'):
                theme = 'system'
            if accent not in ('indigo', 'violet', 'emerald', 'rose', 'amber', 'cyan'):
                accent = 'indigo'
            db.execute("UPDATE users SET display_name=?, theme=?, accent=? WHERE id=?",
                       (display_name, theme, accent, g.user['id']))
            db.commit()
            audit('profile_update')
            flash("Profile updated.", "ok")
            return redirect(url_for('account'))
        if form_type == 'password':
            cur = request.form.get('current', '')
            new = request.form.get('new', '')
            if not check_password_hash(g.user['password_hash'], cur):
                audit('change_password', 'fail', 'wrong current password')
                flash("Current password is wrong.", "err")
            elif not valid_new_password(new):
                flash("New password must be at least 8 characters.", "err")
            else:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user['id']))
                db.commit()
                audit('change_password')
                flash("Password changed.", "ok")
                return redirect(url_for('account'))
    files = db.execute("SELECT * FROM files WHERE user_id=? ORDER BY upload_date DESC", (g.user['id'],)).fetchall()
    used = sum(f['file_size'] or 0 for f in files)
    shares = db.execute("SELECT COUNT(*) c FROM share_links WHERE created_by=?", (g.user['id'],)).fetchone()['c']
    trash_n = db.execute("SELECT COUNT(*) c FROM trash WHERE user_id=?", (g.user['id'],)).fetchone()['c']
    return render(ACCOUNT_BODY, page_title='Account', file_count=len(files), storage_used=used,
                  storage_quota=quota_bytes(g.user), storage_pct=quota_pct(g.user),
                  share_count=shares, trash_count=trash_n)


@app.route('/account/export')
@login_required
def account_export():
    db = get_db()
    files = [dict(r) for r in db.execute("SELECT original_name, file_size, mime, upload_date, folder_id, is_starred FROM files WHERE user_id=?",
                                         (g.user['id'],))]
    folders = [dict(r) for r in db.execute("SELECT id, name, parent_id, created_at FROM folders WHERE user_id=?", (g.user['id'],))]
    shares = [{'file_id': r['file_id'], 'created_at': r['created_at'], 'expires_at': r['expires_at'],
               'download_count': r['download_count']} for r in
              db.execute("SELECT file_id, created_at, expires_at, download_count FROM share_links WHERE created_by=?",
                         (g.user['id'],))]
    payload = {'user': g.user['username'], 'exported_at': now_str(), 'files': files,
               'folders': folders, 'shares': shares}
    buf = io.BytesIO(json.dumps(payload, indent=2).encode('utf-8'))
    audit('account_export')
    return send_file(buf, mimetype='application/json', as_attachment=True, download_name='drz-export.json')


# ============================================================
# Admin
# ============================================================
@app.route('/admin')
@pin_required
def admin_home():
    db = get_db()
    users = db.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM files f WHERE f.user_id=u.id) AS nfiles, "
        "(SELECT COALESCE(SUM(file_size),0) FROM files f WHERE f.user_id=u.id) AS bytes "
        "FROM users u ORDER BY u.id").fetchall()
    total_users = db.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
    cutoff = now_str(utcnow() - timedelta(seconds=ONLINE_WINDOW))
    online_n = db.execute("SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (cutoff,)).fetchone()['c']
    total_files = db.execute("SELECT COUNT(*) c FROM files").fetchone()['c']
    total_bytes = db.execute("SELECT COALESCE(SUM(file_size),0) s FROM files").fetchone()['s']
    total_shares = db.execute("SELECT COUNT(*) c FROM share_links").fetchone()['c']
    activity = []
    for i in range(6, -1, -1):
        day = (utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        c = db.execute("SELECT COUNT(*) c FROM audit_logs WHERE timestamp LIKE ? AND status='ok'",
                       (day + '%',)).fetchone()['c']
        activity.append({'day': day, 'count': c})
    max_activity = max((a['count'] for a in activity), default=0) or 1
    ext_count = {}
    for r in db.execute("SELECT original_name FROM files").fetchall():
        ext = ext_of(r['original_name'] or '') or 'other'
        ext_count[ext] = ext_count.get(ext, 0) + 1
    total_ext = sum(ext_count.values()) or 1
    palette = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#0ea5e9', '#ef4444', '#14b8a6']
    top_ext = sorted(ext_count.items(), key=lambda x: -x[1])[:6]
    donut_segments = []
    acc = 0.0
    for i, (ext, cnt) in enumerate(top_ext):
        pct = cnt / total_ext * 100
        donut_segments.append({'ext': ext, 'count': cnt, 'pct': pct, 'color': palette[i % len(palette)]})
        acc += pct
    other_pct = max(0.0, 100.0 - acc)
    if other_pct > 0.01:
        donut_segments.append({'ext': 'other', 'count': max(0, total_ext - sum(x[1] for x in top_ext)),
                               'pct': other_pct, 'color': '#94a3b8'})
    recent = db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 10").fetchall()
    return render(ADMIN_BODY, page_title='Admin dashboard', users=users, total_users=total_users,
                  online_n=online_n, total_files=total_files, total_bytes=total_bytes,
                  total_shares=total_shares, activity=activity, max_activity=max_activity,
                  donut_segments=donut_segments, recent=recent)


@app.route('/admin/users/create', methods=['POST'])
@pin_required
def admin_create_user():
    username = request.form.get('username', '').strip()
    display_name = (request.form.get('display_name', '') or '').strip()[:60]
    pw = request.form.get('password', '')
    quota_mb = request.form.get('quota_mb', '0')
    try:
        quota = max(0, int(quota_mb or 0)) * 1024 * 1024
    except ValueError:
        quota = 0
    if not valid_username(username):
        flash("Username must be 3–40 characters: letters, numbers, . _ -", "err")
    elif not valid_new_password(pw):
        flash("Password must be at least 8 characters.", "err")
    else:
        try:
            get_db().execute("INSERT INTO users (username, display_name, password_hash, is_admin, quota_bytes) VALUES (?,?,?,?,?)",
                             (username, display_name or username, generate_password_hash(pw),
                              1 if request.form.get('is_admin') else 0, quota))
            get_db().commit()
            audit('admin_create_user', 'ok', username)
            flash(f"User '{username}' created.", "ok")
        except sqlite3.IntegrityError:
            flash("That username already exists.", "err")
    return redirect(url_for('admin_home'))


@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
@pin_required
def admin_update_user(user_id):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not u:
        abort(404)
    display_name = (request.form.get('display_name', '') or '').strip()[:60]
    try:
        quota = max(0, int(request.form.get('quota_mb', '0') or 0)) * 1024 * 1024
    except ValueError:
        quota = 0
    is_admin = 1 if request.form.get('is_admin') else 0
    if u['id'] == g.user['id']:
        is_admin = 1
    if u['username'] == 'admin':
        is_admin = 1
    db.execute("UPDATE users SET display_name=?, quota_bytes=?, is_admin=? WHERE id=?",
               (display_name, quota, is_admin, user_id))
    db.commit()
    audit('admin_update_user', 'ok', u['username'])
    flash("User updated.", "ok")
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
    db.execute("DELETE FROM folders WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    shutil.rmtree(get_user_dir(user_id), ignore_errors=True)
    audit('admin_delete_user', 'ok', u['username'])
    flash(f"User '{u['username']}' deleted.", "ok")
    return redirect(url_for('admin_home'))


@app.route('/admin/logs')
@pin_required
def admin_logs():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    action = request.args.get('action', '')
    page = max(1, request.args.get('page', type=int) or 1)
    per_page = 50
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
    count_sql = sql.replace("SELECT *", "SELECT COUNT(*) c", 1)
    total = get_db().execute(count_sql, params).fetchone()['c']
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = get_db().execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?",
                            params + [per_page, (page - 1) * per_page]).fetchall()
    actions = [r['action'] for r in get_db().execute(
        "SELECT DISTINCT action FROM audit_logs WHERE action IS NOT NULL ORDER BY action").fetchall()]
    fails = get_db().execute("SELECT COUNT(*) c FROM audit_logs WHERE status='fail'").fetchone()['c']
    return render(LOGS_BODY, page_title='Audit log', rows=rows, actions=actions, total=total,
                  fails=fails, q=q, status=status, action=action, page=page, pages=pages)


@app.route('/admin/logs/export')
@pin_required
def admin_logs_export():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    action = request.args.get('action', '')
    sql = "SELECT timestamp, username, ip_address, action, status, failure_reason, user_agent FROM audit_logs WHERE 1=1"
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
    sql += " ORDER BY id DESC LIMIT 20000"
    rows = get_db().execute(sql, params).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['timestamp', 'username', 'ip_address', 'action', 'status', 'failure_reason', 'user_agent'])
    for r in rows:
        writer.writerow([r['timestamp'], r['username'], r['ip_address'], r['action'], r['status'],
                         r['failure_reason'], r['user_agent']])
    data = io.BytesIO(buf.getvalue().encode('utf-8-sig'))
    audit('admin_logs_export', 'ok', f'{len(rows)} rows')
    return send_file(data, mimetype='text/csv', as_attachment=True, download_name='drz-audit-log.csv')


@app.route('/admin/monitor')
@pin_required
def admin_monitor():
    return render(MONITOR_BODY, page_title='Live monitor')


@app.route('/admin/monitor/data')
@pin_required
def admin_monitor_data():
    db = get_db()
    cutoff = now_str(utcnow() - timedelta(seconds=ONLINE_WINDOW))
    users = [{'username': u['username'], 'last_seen': u['last_seen'],
              'online': bool(u['last_seen'] and u['last_seen'] >= cutoff)}
             for u in db.execute("SELECT username, last_seen FROM users ORDER BY last_seen DESC")]
    logs = [dict(r) for r in db.execute(
        "SELECT timestamp, username, ip_address, action, status, failure_reason FROM audit_logs ORDER BY id DESC LIMIT 60")]
    return jsonify(users=users, logs=logs)


@app.route('/admin/settings', methods=['GET', 'POST'])
@pin_required
def admin_settings():
    if request.method == 'POST':
        app_name_val = (request.form.get('app_name', '') or '').strip()[:60] or 'DRZ File Manager'
        set_setting('app_name', app_name_val)
        for key, default, lo, hi in [
            ('max_upload_mb', 2048, 1, 10240),
            ('default_quota_mb', 0, 0, 1048576),
            ('trash_retention_days', 30, 0, 3650),
            ('share_default_hours', 24, 1, 720),
        ]:
            try:
                val = int(request.form.get(key, default) or default)
            except ValueError:
                val = default
            set_setting(key, str(max(lo, min(hi, val))))
        set_setting('allow_registration', '1' if request.form.get('allow_registration') else '0')
        set_setting('footer_note', (request.form.get('footer_note', '') or '')[:120])
        audit('admin_settings', 'ok')
        flash("Settings saved.", "ok")
        return redirect(url_for('admin_settings'))
    s = {k: get_setting(k) for k in ('app_name', 'allow_registration', 'max_upload_mb',
                                     'default_quota_mb', 'trash_retention_days',
                                     'share_default_hours', 'footer_note')}
    return render(SETTINGS_BODY, page_title='Admin settings', s=s)


@app.route('/admin/backup')
@pin_required
def admin_backup():
    db = get_db()
    data = {
        'exported_at': now_str(),
        'users': [dict(r) for r in db.execute(
            "SELECT id, username, display_name, is_admin, quota_bytes, created_at, last_seen FROM users")],
        'folders': [dict(r) for r in db.execute("SELECT * FROM folders")],
        'files': [dict(r) for r in db.execute(
            "SELECT id, user_id, folder_id, original_name, file_size, mime, upload_date, is_starred, download_count FROM files")],
        'shares': [dict(r) for r in db.execute(
            "SELECT id, file_id, created_by, expires_at, download_count, max_downloads, allow_preview, note FROM share_links")],
    }
    buf = io.BytesIO(json.dumps(data, indent=2).encode('utf-8'))
    audit('admin_backup')
    return send_file(buf, mimetype='application/json', as_attachment=True, download_name='drz-backup.json')


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


@app.route('/admin/pin/change', methods=['GET', 'POST'])
@pin_required
def admin_change_pin():
    if request.method == 'POST':
        old, new = request.form.get('old', ''), request.form.get('new', '')
        stored = get_setting('admin_pin_hash')
        if not stored or not check_password_hash(stored, old):
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
    return render(r"""
<div class="card" style="max-width:440px">
  <div class="card-head"><h3>{{ icon('lock', 17) }} Change admin PIN</h3></div>
  <form method="post" class="stack">
    """ + CSRF + r"""
    <div class="field"><label>Current PIN</label><input name="old" type="password" inputmode="numeric" maxlength="6" pattern="\d{6}" placeholder="••••••" required></div>
    <div class="field"><label>New PIN</label><input name="new" type="password" inputmode="numeric" maxlength="6" pattern="\d{6}" placeholder="••••••" required></div>
    <div class="form-actions"><button class="btn" type="submit">{{ icon('key', 14) }} Change PIN</button><a class="btn ghost" href="{{ url_for('admin_home') }}">Cancel</a></div>
  </form>
</div>
""", page_title='Change PIN')


# ============================================================
# JSON API
# ============================================================
@app.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    db = get_db()
    if q:
        files = db.execute("SELECT id, original_name, file_size, mime FROM files WHERE user_id=? AND original_name LIKE ? "
                           "ORDER BY upload_date DESC LIMIT 14", (g.user['id'], f'%{q}%')).fetchall()
        folders = db.execute("SELECT id, name FROM folders WHERE user_id=? AND name LIKE ? ORDER BY name LIMIT 8",
                             (g.user['id'], f'%{q}%')).fetchall()
    else:
        files = db.execute("SELECT id, original_name, file_size, mime FROM files WHERE user_id=? ORDER BY upload_date DESC LIMIT 14",
                           (g.user['id'],)).fetchall()
        folders = []
    return jsonify(
        files=[{'id': f['id'], 'name': f['original_name'], 'size': format_size(f['file_size']),
                'kind': file_kind(f['original_name'])} for f in files],
        folders=[{'id': f['id'], 'name': f['name']} for f in folders])


@app.route('/api/file/<int:file_id>')
@login_required
def api_file(file_id):
    f = own_file_or_404(file_id)
    folder = None
    if f['folder_id']:
        row = get_db().execute("SELECT name FROM folders WHERE id=?", (f['folder_id'],)).fetchone()
        folder = row['name'] if row else None
    shares = get_db().execute("SELECT COUNT(*) c FROM share_links WHERE file_id=?", (file_id,)).fetchone()['c']
    return jsonify(id=f['id'], name=f['original_name'], size=format_size(f['file_size']),
                   bytes=f['file_size'], mime=f['mime'], kind=file_kind(f['original_name']),
                   folder=folder or 'Home', upload_date=f['upload_date'],
                   download_count=f['download_count'], share_count=shares)


# ============================================================
# Public assets
# ============================================================
@app.route('/favicon.svg')
def favicon():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#6366f1"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs><rect width="64" height="64" rx="16" fill="url(#g)"/><path d="M17 19a4 4 0 0 1 4-4h8l4 5h14a4 4 0 0 1 4 4v21a4 4 0 0 1-4 4H21a4 4 0 0 1-4-4z" fill="none" stroke="#fff" stroke-width="3.5" stroke-linejoin="round"/></svg>'''
    return Response(svg, mimetype='image/svg+xml', headers={'Cache-Control': 'public, max-age=86400'})


@app.route('/manifest.webmanifest')
def manifest():
    data = {
        'name': app_name(), 'short_name': 'DRZ Files', 'description': 'A fast, private file manager.',
        'start_url': '/', 'display': 'standalone', 'background_color': '#f5f7fc',
        'theme_color': '#6366f1', 'orientation': 'any',
        'icons': [{'src': '/favicon.svg', 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'}],
    }
    return Response(json.dumps(data), mimetype='application/manifest+json')


@app.route('/sw.js')
def service_worker():
    js = r'''
const CACHE = 'drz-v1';
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', e => {
  if(e.request.method !== 'GET' || new URL(e.request.url).origin !== location.origin) return;
  if(e.request.mode === 'navigate'){
    e.respondWith(fetch(e.request).catch(() => caches.match('/')));
  }
});
'''
    return Response(js, mimetype='application/javascript', headers={'Cache-Control': 'no-cache'})


@app.route('/robots.txt')
def robots():
    return Response("User-agent: *\nDisallow: /admin\nDisallow: /api/\nDisallow: /s/\n", mimetype='text/plain')


@app.route('/health')
def health():
    try:
        get_db().execute("SELECT 1").fetchone()
        ok = True
    except Exception:
        ok = False
    return jsonify(status='ok' if ok else 'degraded', app=app_name(), time=now_str()), (200 if ok else 503)


# ============================================================
# Error handlers & security headers
# ============================================================
def _error_page(code, title, message):
    body = f"""
<div class="empty" style="padding:70px 18px">
  <div class="empty-ico" style="width:84px;height:84px;border-radius:24px">{icon('alert', 38)}</div>
  <h4 style="font-size:22px">{code} — {title}</h4>
  <p>{message}</p>
  <a class="btn mt-1" href="{url_for('index')}">{icon('arrow-left', 14)} Back home</a>
</div>
"""
    return render(body, page_title=f'{code} {title}')


@app.errorhandler(400)
def e400(e):
    return _error_page(400, 'Bad request', getattr(e, 'description', 'The request could not be processed.')), 400


@app.errorhandler(403)
def e403(e):
    return _error_page(403, 'Forbidden', "You don't have access to this page."), 403


@app.errorhandler(404)
def e404(e):
    return _error_page(404, 'Not found', "The page or file you requested doesn't exist."), 404


@app.errorhandler(410)
def e410(e):
    return _error_page(410, 'Gone', "This link has expired or is no longer available."), 410


@app.errorhandler(413)
def e413(e):
    return _error_page(413, 'File too large', "The upload exceeded the server limit. Try a smaller file or raise the limit."), 413


@app.errorhandler(500)
def e500(e):
    return _error_page(500, 'Server error', "Something went wrong on our side. Please try again."), 500


@app.after_request
def security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('X-XSS-Protection', '1; mode=block')
    resp.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    resp.headers.setdefault('Content-Security-Policy',
                            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                            "frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
                            "connect-src 'self'")
    if request.is_secure:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    if resp.mimetype == 'text/html':
        resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


# ============================================================
# Vercel route dispatcher
# ============================================================
_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']


@app.route('/api/index', defaults={'_path': ''}, methods=_METHODS)
@app.route('/api/index/', defaults={'_path': ''}, methods=_METHODS)
@app.route('/api/index/<path:_path>', methods=_METHODS)
def _vercel_dispatch(_path):
    original_path = '/' + _path if _path else '/'
    request.environ['ORIGINAL_PATH'] = original_path
    request.environ['PATH_INFO'] = original_path
    for key in ('path', 'full_path', 'url', 'base_url', 'path_url', 'host_url'):
        request.__dict__.pop(key, None)
    if _path == 'logout' and request.method == 'GET':
        session.clear()
        return redirect('/')
    if not g.user and _path.split('/')[0] in {'account', 'trash', 'admin', 'shares', 'starred', 'recent'}:
        return redirect('/login?next=' + original_path)
    endpoint, values = app.url_map.bind_to_environ(request.environ).match()
    return app.view_functions[endpoint](**values)


if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', 5000)), debug=False)




