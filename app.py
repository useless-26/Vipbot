from flask import Flask, request, jsonify, send_from_directory, session, redirect, render_template, url_for
from flask_cors import CORS
import zipfile
import io
import base64
import requests
import time
import os
from urllib.parse import quote
from datetime import datetime, timedelta
from functools import wraps
import hashlib
import secrets
import string
import sqlite3
import json
import re

# Initialize database
DB_PATH = 'zipush.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table with github_token column
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,
            telegram_id TEXT,
            telegram_bot_token TEXT,
            bot_active INTEGER DEFAULT 0,
            github_token TEXT
        )
    ''')
    
    # API Keys table
    c.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            user_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Upload history
    c.execute('''
        CREATE TABLE IF NOT EXISTS upload_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            repo_name TEXT,
            file_path TEXT,
            file_size INTEGER,
            action TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Image cache
    c.execute('''
        CREATE TABLE IF NOT EXISTS image_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_hash TEXT UNIQUE,
            image_url TEXT,
            file_path TEXT,
            repo_name TEXT,
            user_id INTEGER,
            file_size INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # VIP logs
    c.execute('''
        CREATE TABLE IF NOT EXISTS vip_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            user_id INTEGER,
            action TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (admin_id) REFERENCES users (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Themes
    c.execute('''
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            primary_color TEXT DEFAULT '#00f5ff',
            secondary_color TEXT DEFAULT '#ff2d78',
            background_color TEXT DEFAULT '#060811',
            card_color TEXT DEFAULT '#0c1020',
            text_color TEXT DEFAULT '#c8d4f0',
            accent_color TEXT DEFAULT '#b44dff',
            user_id INTEGER,
            is_active INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Bot logs
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            status TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Projects
    c.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            repo_name TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Create default admin user
    admin_exists = c.execute('SELECT id FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin_exists:
        password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        c.execute('''
            INSERT INTO users (username, password_hash, is_admin, is_vip)
            VALUES (?, ?, ?, ?)
        ''', ('admin', password_hash, 1, 1))
    
    # Create default themes
    default_themes = [
        ('Cyber Blue', '#00f5ff', '#ff2d78', '#060811', '#0c1020', '#c8d4f0', '#b44dff'),
        ('Neon Pink', '#ff2d78', '#00f5ff', '#0a0510', '#150a20', '#ffb6c1', '#ff6b9d'),
        ('Forest Green', '#0dff8c', '#ffb830', '#050d08', '#0a1a10', '#a8e6cf', '#45b7d1'),
        ('Sunset Orange', '#ff6b35', '#ffd700', '#1a0804', '#2a1008', '#f5cba7', '#e74c3c'),
        ('Purple Haze', '#9b59b6', '#3498db', '#080410', '#100820', '#d5b8ff', '#6c5ce7'),
        ('Ocean Blue', '#1a8cff', '#00d4ff', '#040a18', '#081430', '#b8d4ff', '#00bcd4'),
        ('Crimson Red', '#ff1744', '#ff6d00', '#180408', '#280a10', '#ffb8b8', '#ff3d00')
    ]
    
    for theme in default_themes:
        exists = c.execute('SELECT id FROM themes WHERE name = ?', (theme[0],)).fetchone()
        if not exists:
            c.execute('''
                INSERT INTO themes (name, primary_color, secondary_color, background_color, card_color, text_color, accent_color, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (*theme, 1 if theme[0] == 'Cyber Blue' else 0))
    
    conn.commit()
    conn.close()

init_db()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')
CORS(app, supports_credentials=True)

GH_API = "https://api.github.com"

def gh_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

# ============ DATABASE HELPER FUNCTIONS ============

def get_user(username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    user = c.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    user = c.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

def create_user(username, password, is_admin=False, is_vip=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    try:
        c.execute('''
            INSERT INTO users (username, password_hash, is_admin, is_vip)
            VALUES (?, ?, ?, ?)
        ''', (username, password_hash, 1 if is_admin else 0, 1 if is_vip else 0))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        return None

def generate_api_key(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    key = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    key = f'zipush_{key}'
    c.execute('''
        INSERT INTO api_keys (key, user_id)
        VALUES (?, ?)
    ''', (key, user_id))
    conn.commit()
    conn.close()
    return key

def verify_api_key(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    result = c.execute('''
        SELECT user_id, is_active FROM api_keys 
        WHERE key = ? AND is_active = 1
        AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
    ''', (key,)).fetchone()
    conn.close()
    return result

def save_github_token(user_id, token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        UPDATE users SET github_token = ? WHERE id = ?
    ''', (token, user_id))
    conn.commit()
    conn.close()

def get_github_token(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    result = c.execute('''
        SELECT github_token FROM users WHERE id = ?
    ''', (user_id,)).fetchone()
    conn.close()
    return result[0] if result else None

def get_user_images(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute('''
        SELECT image_hash, image_url, file_path, repo_name, file_size, created_at 
        FROM image_cache 
        WHERE user_id = ?
        ORDER BY created_at DESC
    ''', (user_id,)).fetchall()
    conn.close()
    return rows

def delete_image(image_hash, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM image_cache WHERE image_hash = ? AND user_id = ?', (image_hash, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute('''
        SELECT id, username, is_admin, is_vip, is_banned, created_at, last_login, telegram_id, bot_active 
        FROM users 
        ORDER BY id
    ''').fetchall()
    conn.close()
    return rows

def ban_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET is_banned = 1 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET is_banned = 0 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def promote_to_vip(admin_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET is_vip = 1 WHERE id = ?', (user_id,))
    c.execute('''
        INSERT INTO vip_logs (admin_id, user_id, action)
        VALUES (?, ?, ?)
    ''', (admin_id, user_id, 'promote_to_vip'))
    conn.commit()
    conn.close()

def demote_from_vip(admin_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET is_vip = 0 WHERE id = ?', (user_id,))
    c.execute('''
        INSERT INTO vip_logs (admin_id, user_id, action)
        VALUES (?, ?, ?)
    ''', (admin_id, user_id, 'demote_from_vip'))
    conn.commit()
    conn.close()

def get_upload_history(user_id=None, limit=100):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if user_id:
        rows = c.execute('''
            SELECT * FROM upload_history 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (user_id, limit)).fetchall()
    else:
        rows = c.execute('''
            SELECT * FROM upload_history 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (limit,)).fetchall()
    conn.close()
    return rows

def get_user_projects(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute('''
        SELECT * FROM projects 
        WHERE user_id = ? AND is_active = 1
        ORDER BY created_at DESC
    ''', (user_id,)).fetchall()
    conn.close()
    return rows

def get_user_bot_token(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    result = c.execute('SELECT telegram_bot_token, bot_active FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return result if result else (None, 0)

def update_bot_token(user_id, token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET telegram_bot_token = ? WHERE id = ?', (token, user_id))
    conn.commit()
    conn.close()

def toggle_bot(user_id, active):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET bot_active = ? WHERE id = ?', (1 if active else 0, user_id))
    conn.commit()
    conn.close()

def log_bot_action(user_id, action, status, message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO bot_logs (user_id, action, status, message)
        VALUES (?, ?, ?, ?)
    ''', (user_id, action, status, message))
    conn.commit()
    conn.close()

def get_bot_logs(user_id=None, limit=50):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if user_id:
        rows = c.execute('''
            SELECT * FROM bot_logs 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (user_id, limit)).fetchall()
    else:
        rows = c.execute('''
            SELECT * FROM bot_logs 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (limit,)).fetchall()
    conn.close()
    return rows

def get_active_theme(user_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if user_id:
        theme = c.execute('''
            SELECT * FROM themes 
            WHERE user_id = ? AND is_active = 1
        ''', (user_id,)).fetchone()
        if not theme:
            theme = c.execute('''
                SELECT * FROM themes 
                WHERE is_active = 1 AND user_id IS NULL
                LIMIT 1
            ''').fetchone()
    else:
        theme = c.execute('''
            SELECT * FROM themes 
            WHERE is_active = 1 AND user_id IS NULL
            LIMIT 1
        ''').fetchone()
    conn.close()
    return theme

def set_user_theme(user_id, theme_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE themes SET is_active = 0 WHERE user_id = ?', (user_id,))
    c.execute('UPDATE themes SET is_active = 1 WHERE name = ? AND (user_id = ? OR user_id IS NULL)', (theme_name, user_id))
    conn.commit()
    conn.close()

# ============ AUTH DECORATORS ============

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            # Check for API key in headers
            api_key = request.headers.get('X-API-Key')
            if api_key:
                key_info = verify_api_key(api_key)
                if key_info:
                    user_id = key_info[0]
                    session['user_id'] = user_id
        
        if not user_id:
            # For API requests return 401, for page requests redirect to login
            if request.path.startswith('/api/'):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for('login_page'))
        
        user = get_user_by_id(user_id)
        if not user:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({"error": "User not found"}), 401
            return redirect(url_for('login_page'))
        
        # Check if user is banned
        if user[5] == 1:  # is_banned
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({"error": "Your account has been banned"}), 403
            return render_template('login.html', error='Your account has been banned')
        
        request.user_id = user_id
        request.user = {
            'id': user[0],
            'username': user[1],
            'is_admin': bool(user[3]),
            'is_vip': bool(user[4]),
            'is_banned': bool(user[5])
        }
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not request.user['is_admin']:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Admin access required"}), 403
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ============ PAGE ROUTES ============

@app.route('/')
def index():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login_page'))

@app.route('/login')
def login_page():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_user_by_id(request.user_id)
    theme = get_active_theme(request.user_id)
    projects = get_user_projects(request.user_id)
    images = get_user_images(request.user_id)
    history = get_upload_history(request.user_id, 10)
    return render_template('dashboard.html', user=user, theme=theme, projects=projects, images=images, history=history)

@app.route('/upload')
@login_required
def upload_page():
    user = get_user_by_id(request.user_id)
    theme = get_active_theme(request.user_id)
    return render_template('upload.html', user=user, theme=theme)

@app.route('/images')
@login_required
def images_page():
    user = get_user_by_id(request.user_id)
    theme = get_active_theme(request.user_id)
    images = get_user_images(request.user_id)
    return render_template('images.html', user=user, theme=theme, images=images)

@app.route('/bot-control')
@login_required
def bot_control():
    user = get_user_by_id(request.user_id)
    theme = get_active_theme(request.user_id)
    bot_token, bot_active = get_user_bot_token(request.user_id)
    bot_logs = get_bot_logs(request.user_id)
    return render_template('bot_control.html', user=user, theme=theme, bot_token=bot_token, bot_active=bot_active, bot_logs=bot_logs)

@app.route('/settings')
@login_required
def settings_page():
    user = get_user_by_id(request.user_id)
    theme = get_active_theme(request.user_id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute('SELECT * FROM themes WHERE user_id = ? OR user_id IS NULL', (request.user_id,)).fetchall()
    conn.close()
    return render_template('settings.html', user=user, theme=theme, themes=rows)

@app.route('/admin')
@admin_required
def admin_page():
    user = get_user_by_id(request.user_id)
    theme = get_active_theme(request.user_id)
    users = get_all_users()
    history = get_upload_history(limit=50)
    return render_template('admin.html', user=user, theme=theme, users=users, history=history)

# ============ AUTHENTICATION ROUTES ============

@app.route('/api/me', methods=['GET'])
@login_required
def me():
    user = get_user_by_id(request.user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user[0],
        "username": user[1],
        "is_admin": bool(user[3]),
        "is_vip": bool(user[4]),
        "is_banned": bool(user[5]),
        "created_at": user[6],
        "last_login": user[7]
    })

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    user = get_user(username)
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
    
    if user[5] == 1:  # is_banned
        return jsonify({"error": "Your account has been banned"}), 403
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if user[2] != password_hash:
        return jsonify({"error": "Invalid credentials"}), 401
    
    session['user_id'] = user[0]
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user[0],))
    conn.commit()
    conn.close()
    
    return jsonify({
        "success": True,
        "user": {
            "id": user[0],
            "username": user[1],
            "is_admin": bool(user[3]),
            "is_vip": bool(user[4]),
            "is_banned": bool(user[5])
        }
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    api_key = data.get('api_key', '').strip()
    
    if api_key:
        key_info = verify_api_key(api_key)
        if not key_info:
            return jsonify({"error": "Invalid registration key"}), 401
        is_vip = True
    else:
        is_vip = False
    
    if len(username) < 3 or len(password) < 6:
        return jsonify({"error": "Username (min 3 chars) and password (min 6 chars) required"}), 400
    
    user_id = create_user(username, password, is_admin=False, is_vip=is_vip)
    if not user_id:
        return jsonify({"error": "Username already exists"}), 409
    
    return jsonify({
        "success": True,
        "message": "User created successfully",
        "is_vip": is_vip
    })

@app.route('/api/get-token', methods=['GET'])
@login_required
def get_stored_token():
    token = get_github_token(request.user_id)
    if not token:
        return jsonify({"error": "No token found"}), 404
    return jsonify({"token": token})

# ============ GITHUB CONNECT ROUTE ============

@app.route('/api/connect', methods=['POST'])
@login_required
def connect():
    data = request.get_json()
    token = (data or {}).get("token", "").strip()
    if not token:
        return jsonify({"error": "Token is required"}), 400
    
    save_github_token(request.user_id, token)
    
    r = requests.get(f"{GH_API}/user", headers=gh_headers(token))
    if not r.ok:
        msg = r.json().get("message", "Authentication failed")
        return jsonify({"error": msg}), 401
    
    user = r.json()
    
    repos = []
    page = 1
    while True:
        rr = requests.get(
            f"{GH_API}/user/repos?per_page=100&page={page}&sort=updated",
            headers=gh_headers(token)
        )
        if not rr.ok:
            break
        batch = rr.json()
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    
    return jsonify({
        "user": {
            "login": user["login"],
            "name": user.get("name") or user["login"],
            "avatar_url": user["avatar_url"]
        },
        "repos": [
            {"name": r["name"], "private": r["private"], "updated_at": r["updated_at"]}
            for r in repos
        ]
    })

# ============ ADMIN ROUTES ============

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_get_users():
    users = get_all_users()
    return jsonify([{
        "id": u[0],
        "username": u[1],
        "is_admin": bool(u[3]),
        "is_vip": bool(u[4]),
        "is_banned": bool(u[5]),
        "created_at": u[6],
        "last_login": u[7],
        "telegram_id": u[8],
        "bot_active": bool(u[9])
    } for u in users])

@app.route('/api/admin/users/<int:user_id>/ban', methods=['POST'])
@admin_required
def ban_user_route(user_id):
    if user_id == request.user_id:
        return jsonify({"error": "Cannot ban yourself"}), 400
    ban_user(user_id)
    return jsonify({"success": True})

@app.route('/api/admin/users/<int:user_id>/unban', methods=['POST'])
@admin_required
def unban_user_route(user_id):
    unban_user(user_id)
    return jsonify({"success": True})

@app.route('/api/admin/users/<int:user_id>/vip', methods=['POST'])
@admin_required
def toggle_vip(user_id):
    data = request.get_json()
    action = data.get('action', 'promote')
    
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    if action == 'promote':
        promote_to_vip(request.user_id, user_id)
        message = f"User {user[1]} promoted to VIP"
    else:
        demote_from_vip(request.user_id, user_id)
        message = f"User {user[1]} demoted from VIP"
    
    return jsonify({
        "success": True,
        "message": message
    })

@app.route('/api/admin/generate-key', methods=['POST'])
@admin_required
def admin_generate_key():
    data = request.get_json()
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({"error": "User ID required"}), 400
    
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    key = generate_api_key(user_id)
    return jsonify({
        "success": True,
        "api_key": key,
        "user_id": user_id,
        "username": user[1]
    })

# ============ THEME ROUTE ============

@app.route('/api/theme/set', methods=['POST'])
@login_required
def set_theme():
    data = request.get_json()
    theme_name = data.get('theme_name')
    
    if not theme_name:
        return jsonify({"error": "Theme name required"}), 400
    
    set_user_theme(request.user_id, theme_name)
    return jsonify({"success": True})

# ============ IMAGE ROUTES ============

@app.route('/api/images/delete', methods=['POST'])
@login_required
def delete_image_route():
    data = request.get_json()
    image_id = data.get('image_id')
    
    if not image_id:
        return jsonify({"error": "Image ID required"}), 400
    
    delete_image(image_id, request.user_id)
    return jsonify({"success": True})

@app.route('/api/image/<image_id>')
def get_image(image_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    result = c.execute('''
        SELECT image_url FROM image_cache WHERE image_hash = ?
    ''', (image_id,)).fetchone()
    conn.close()
    
    if not result:
        return jsonify({"error": "Image not found"}), 404
    
    return redirect(result[0])

# ============ BOT ROUTES ============

@app.route('/api/bot/token', methods=['POST'])
@login_required
def update_bot_token_route():
    data = request.get_json()
    token = data.get('token', '').strip()
    
    if not token:
        return jsonify({"error": "Token required"}), 400
    
    update_bot_token(request.user_id, token)
    log_bot_action(request.user_id, 'update_token', 'success', 'Bot token updated')
    return jsonify({"success": True})

@app.route('/api/bot/toggle', methods=['POST'])
@login_required
def toggle_bot_route():
    data = request.get_json()
    active = data.get('active', False)
    
    toggle_bot(request.user_id, active)
    log_bot_action(request.user_id, 'toggle_bot', 'success', f'Bot {"started" if active else "stopped"}')
    return jsonify({"success": True})

@app.route('/api/bot/status', methods=['GET'])
@login_required
def get_bot_status():
    token, active = get_user_bot_token(request.user_id)
    return jsonify({
        "has_token": bool(token),
        "is_active": bool(active)
    })

# ============ PUSH ROUTE ============

def create_folder_recursive(owner, repo_name, folder_path, token, logs, created_cache):
    if not folder_path or folder_path.endswith('/'):
        folder_path = folder_path.rstrip('/')
    
    if not folder_path:
        return True
    
    cache_key = f"{owner}/{repo_name}/{folder_path}"
    if cache_key in created_cache:
        return True
    
    if '/' in folder_path:
        parent = '/'.join(folder_path.split('/')[:-1])
        if parent:
            if not create_folder_recursive(owner, repo_name, parent, token, logs, created_cache):
                return False
    
    check_url = f"{GH_API}/repos/{owner}/{repo_name}/contents/{quote(folder_path)}"
    hdrs = gh_headers(token)
    r = requests.get(check_url, headers=hdrs)
    
    if r.status_code == 200:
        created_cache.add(cache_key)
        return True
    
    gitkeep_path = f"{folder_path}/.gitkeep"
    content_b64 = base64.b64encode(b"").decode()
    
    payload = {
        "message": f"Create folder {folder_path}",
        "content": content_b64
    }
    
    put_url = f"{GH_API}/repos/{owner}/{repo_name}/contents/{quote(gitkeep_path)}"
    response = requests.put(put_url, json=payload, headers=hdrs)
    
    if response.ok:
        logs.append({"type": "info", "text": f"Created folder: {folder_path}/"})
        created_cache.add(cache_key)
        return True
    return False

@app.route('/api/push', methods=['POST'])
@login_required
def push():
    token = request.form.get("token", "").strip()
    mode = request.form.get("mode", "existing")
    repo_name = request.form.get("repo_name", "").strip()
    private = request.form.get("private", "false") == "true"
    zip_file = request.files.get("zip_file")
    project_name = request.form.get("project_name", "").strip()
    
    if not token:
        stored_token = get_github_token(request.user_id)
        if stored_token:
            token = stored_token
        else:
            return jsonify({"error": "Missing token"}), 400
    
    if not repo_name:
        return jsonify({"error": "Missing repo name"}), 400
    if not zip_file:
        return jsonify({"error": "No ZIP file provided"}), 400
    
    hdrs = gh_headers(token)
    logs = []
    
    user_r = requests.get(f"{GH_API}/user", headers=hdrs)
    if not user_r.ok:
        return jsonify({"error": "Invalid token"}), 401
    owner = user_r.json()["login"]
    
    def log(t, text):
        logs.append({"type": t, "text": text})
    
    def push_file(path, content_b64, message, created_cache):
        if '/' in path:
            parent_dir = '/'.join(path.split('/')[:-1])
            if parent_dir:
                if not create_folder_recursive(owner, repo_name, parent_dir, token, logs, created_cache):
                    return False, "Failed to create parent folder"
        
        sha = None
        encoded_path = quote(path)
        ex = requests.get(
            f"{GH_API}/repos/{owner}/{repo_name}/contents/{encoded_path}",
            headers=hdrs
        )
        if ex.status_code == 200:
            sha = ex.json().get("sha")
    
        payload = {"message": message, "content": content_b64}
        if sha:
            payload["sha"] = sha
    
        r = requests.put(
            f"{GH_API}/repos/{owner}/{repo_name}/contents/{encoded_path}",
            json=payload,
            headers=hdrs
        )
        
        if r.status_code in [200, 201]:
            return True, None
        else:
            error_msg = r.json().get("message", "Unknown error")
            return False, error_msg
    
    if mode == "new":
        log("info", f'Creating repository "{repo_name}"...')
        r = requests.post(
            f"{GH_API}/user/repos",
            json={"name": repo_name, "private": private, "auto_init": False},
            headers=hdrs
        )
        if not r.ok:
            error_detail = r.json().get("message", "Repo creation failed")
            return jsonify({"error": error_detail}), 400
        log("success", f'Repository "{repo_name}" created')
        time.sleep(1.5)
    
    verify_url = f"{GH_API}/repos/{owner}/{repo_name}"
    verify_resp = requests.get(verify_url, headers=hdrs)
    if not verify_resp.ok:
        return jsonify({"error": f"Repository '{repo_name}' not found"}), 404
    
    total_files_pushed = 0
    created_folders_cache = set()
    
    zip_bytes = zip_file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid ZIP file"}), 400
    
    file_list = [info for info in zf.infolist() if not info.is_dir()]
    
    if not file_list:
        return jsonify({"error": "ZIP file contains no files"}), 400
    
    log("info", f"Found {len(file_list)} file(s) in ZIP")
    file_list.sort(key=lambda x: x.filename)
    
    for file_info in file_list:
        file_path = file_info.filename
        if file_path.startswith('./'):
            file_path = file_path[2:]
        if file_path.startswith('/'):
            file_path = file_path[1:]
        
        if not file_path or file_path.endswith('/'):
            continue
        
        try:
            file_content = zf.read(file_info)
            content_b64 = base64.b64encode(file_content).decode()
            
            log("info", f"Uploading: {file_path}")
            success, error = push_file(file_path, content_b64, f"Add {file_path}", created_folders_cache)
            
            if success:
                log("success", f"✓ {file_path}")
                total_files_pushed += 1
                
                # Check if it's an image
                image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico']
                if any(file_path.lower().endswith(ext) for ext in image_extensions):
                    image_id = hashlib.md5(f"{owner}/{repo_name}/{file_path}".encode()).hexdigest()[:12]
                    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo_name}/main/{file_path}"
                    
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute('''
                        INSERT OR REPLACE INTO image_cache (image_hash, image_url, file_path, repo_name, user_id, file_size)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (image_id, raw_url, file_path, repo_name, request.user_id, len(file_content)))
                    conn.commit()
                    conn.close()
            else:
                log("error", f"✗ {file_path} - {error}")
                
        except Exception as e:
            log("error", f"✗ {file_path} - {str(e)}")
    
    log("success", f"Pushed {total_files_pushed} file(s)")
    
    repo_url = f"https://github.com/{owner}/{repo_name}"
    
    return jsonify({
        "logs": logs,
        "repo_url": repo_url,
        "owner": owner,
        "total_files": total_files_pushed
    })

# ============ HEALTH CHECK ============

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, port=port, host='0.0.0.0')