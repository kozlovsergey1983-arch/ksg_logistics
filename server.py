"""KSG CRM: authenticated shared workspace. PostgreSQL in production."""
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from excel_import import read_contacts, merge_contacts

from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).parent
COLLECTIONS = ('items', 'partners', 'deals', 'calls', 'notes', 'templates')
SCHEMA = [
    'CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 1)',
    'CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires BIGINT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, created BIGINT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS attempts (login TEXT PRIMARY KEY, count INTEGER NOT NULL, reset BIGINT NOT NULL)',
]


def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=os.getenv('SECRET_KEY'), DATABASE_URL=os.getenv('DATABASE_URL'),
                      APP_ENV=os.getenv('APP_ENV', 'production'), SQLITE_PATH=os.getenv('SQLITE_PATH', '/tmp/ksg-dev.sqlite'),
                      ADMIN_LOGIN=os.getenv('ADMIN_LOGIN', 'admin'), ADMIN_NAME=os.getenv('ADMIN_NAME', 'Сергей'),
                      ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD'), MAX_CONTENT_LENGTH=5 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=True)
    if config:
        app.config.update(config)
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('SECRET_KEY must contain at least 32 random characters')
    if app.config['APP_ENV'] == 'production' and not app.config['DATABASE_URL']:
        raise RuntimeError('DATABASE_URL (PostgreSQL) is required in production')
    if app.config['APP_ENV'] != 'production':
        app.config['SESSION_COOKIE_SECURE'] = False

    @contextmanager
    def db():
        if app.config['DATABASE_URL']:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(app.config['DATABASE_URL'], row_factory=dict_row, connect_timeout=10)
            def run(sql, params=()):
                return conn.execute(sql.replace('?', '%s'), params)
        else:
            conn = sqlite3.connect(app.config['SQLITE_PATH'], timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            def run(sql, params=()):
                return conn.execute(sql, params)
        try:
            yield run
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def audit(q, actor, action, target=''):
        q('INSERT INTO audit VALUES (?,?,?,?,?)', (str(uuid.uuid4()), int(time.time()), actor, action, target))

    def strong_password(p):
        if not isinstance(p, str) or not 12 <= len(p) <= 256:
            abort(400, 'Пароль должен содержать от 12 до 256 символов')
        return p

    def valid_login(v):
        if not isinstance(v, str) or not re.fullmatch(r'[a-zA-Z0-9_.@+-]{3,100}', v):
            abort(400, 'Логин: 3–100 латинских букв, цифр или символов _.@+-')
        return v.lower()

    def name(v):
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 100:
            abort(400, 'Укажи имя длиной до 100 символов')
        return v.strip()

    def public_user(u):
        return {k: u[k] for k in ('id', 'login', 'name', 'role', 'active', 'must_change')}

    def initialize():
        with db() as q:
            if app.config['DATABASE_URL']:
                q('SELECT pg_advisory_xact_lock(724199)')
            for sql in SCHEMA:
                q(sql)
            if not q('SELECT id FROM users LIMIT 1').fetchone():
                password = app.config['ADMIN_PASSWORD']
                if not isinstance(password, str) or not 12 <= len(password) <= 256:
                    raise RuntimeError('For first start set ADMIN_PASSWORD (12–256 characters)')
                login = app.config['ADMIN_LOGIN'].lower()
                if not re.fullmatch(r'[a-zA-Z0-9_.@+-]{3,100}', login):
                    raise RuntimeError('Invalid ADMIN_LOGIN')
                q('INSERT INTO users VALUES (?,?,?,?,?,?,?)', (str(uuid.uuid4()), login, app.config['ADMIN_NAME'],
                  generate_password_hash(password), 'owner', 1, 1))
            if not q('SELECT id FROM workspace WHERE id=1').fetchone():
                seed = json.loads((ROOT / 'seed.json').read_text())
                q('INSERT INTO workspace VALUES (1,0,?)', (json.dumps(seed, ensure_ascii=False),))

    initialize()
    app.crm_db = db

    @app.errorhandler(HTTPException)
    def http_error(e):
        return jsonify(error=e.description), e.code

    @app.errorhandler(Exception)
    def unexpected(e):
        app.logger.exception('Request failed')
        return jsonify(error='Ошибка сервера. Изменение не подтверждено; повтори позже.'), 500

    @app.before_request
    def authentication():
        if not request.path.startswith('/api/'):
            return
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            csrf = request.headers.get('X-CSRF-Token', '')
            if not csrf or not secrets.compare_digest(csrf, session.get('csrf', '')):
                abort(403, 'Обнови страницу и повтори действие (CSRF)')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                abort(403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.path != '/api/import/excel' and not isinstance(request.get_json(silent=True), dict):
            abort(400, 'Ожидается JSON-объект')
        if request.path in ('/api/login', '/api/session'):
            return
        u = current_user()
        if not u:
            abort(401, 'Войди в систему')
        g.user = u
        if u['must_change'] and request.path not in ('/api/password', '/api/logout'):
            abort(403, 'Сначала смени временный пароль')

    def current_user():
        token = session.get('token')
        if not token:
            return None
        with db() as q:
            return q('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>? AND u.active=1',
                     (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if app.config['APP_ENV'] == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.get('/healthz')
    def health():
        with db() as q:
            q('SELECT 1')
        return jsonify(status='ok')

    @app.get('/')
    def index():
        return send_from_directory(ROOT / 'public', 'index.html')

    @app.get('/<filename>')
    def assets(filename):
        if filename not in ('app.js', 'style.css', 'auth.js'):
            abort(404)
        return send_from_directory(ROOT / 'public', filename)

    @app.get('/api/session')
    def who():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        u = current_user()
        return jsonify(user=public_user(u) if u else None, csrf=session['csrf'])

    @app.post('/api/login')
    def login():
        data = request.get_json() or {}
        login = valid_login(data.get('login'))
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 256:
            abort(400)
        now = int(time.time())
        limited = False
        with db() as q:
            q('INSERT INTO attempts VALUES (?,0,?) ON CONFLICT(login) DO NOTHING', (login, now+900))
            q('UPDATE attempts SET count=0,reset=? WHERE login=? AND reset<=?', (now+900, login, now))
            updated = q('UPDATE attempts SET count=count+1 WHERE login=? AND count<10', (login,))
            limited = updated.rowcount == 0
            u = q('SELECT * FROM users WHERE login=?', (login,)).fetchone()
        if limited:
            abort(429, 'Слишком много попыток. Повтори через 15 минут')
        # Equal-cost password verification also for unknown usernames.
        stored = u['password'] if u else DUMMY_HASH
        correct = check_password_hash(stored, password)
        if not u or not u['active'] or not correct:
            abort(401, 'Неверный логин или пароль')
        with db() as q:
            q('DELETE FROM attempts WHERE login=?', (login,))
            q('DELETE FROM sessions WHERE expires<=?', (now,))
            token = secrets.token_urlsafe(32)
            q('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), u['id'], now+43200))
            audit(q, u['login'], 'Вход')
        session.clear()
        session.update(token=token, csrf=secrets.token_urlsafe(32))
        return jsonify(user=public_user(u), csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        with db() as q:
            q('DELETE FROM sessions WHERE token=?', (hashlib.sha256(session['token'].encode()).hexdigest(),))
        session.clear()
        return jsonify(ok=True)

    @app.post('/api/password')
    def password():
        data = request.get_json() or {}
        old = data.get('old', '')
        if not isinstance(old, str) or not check_password_hash(g.user['password'], old):
            abort(400, 'Текущий пароль неверен')
        new = strong_password(data.get('password'))
        if new == old:
            abort(400, 'Новый пароль должен отличаться')
        with db() as q:
            q('UPDATE users SET password=?,must_change=0 WHERE id=?', (generate_password_hash(new), g.user['id']))
            q('DELETE FROM sessions WHERE user_id=?', (g.user['id'],))
            audit(q, g.user['login'], 'Смена пароля')
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/state')
    def state():
        with db() as q:
            row = q('SELECT revision,data FROM workspace WHERE id=1').fetchone()
        return jsonify(revision=row['revision'], data=json.loads(row['data']))

    def validate_state(data):
        if not isinstance(data, dict) or set(data) != set(COLLECTIONS):
            abort(400, 'Неверный формат рабочей базы')
        for key in COLLECTIONS:
            if not isinstance(data[key], list) or len(data[key]) > 10000:
                abort(400, 'Слишком много записей или неверный формат')
            ids = set()
            for row in data[key]:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', row['id']) or row['id'] in ids:
                    abort(400, 'Неверный или повторяющийся идентификатор записи')
                ids.add(row['id'])
                for k,v in row.items():
                    if k == 'docs' and key == 'partners':
                        if not isinstance(v,list) or any(type(x) is not int or not 0 <= x < 7 for x in v): abort(400)
                    elif type(v) not in (str,int,float,bool) and v is not None:
                        abort(400, 'Недопустимое значение записи')
                    elif isinstance(v,str) and len(v)>50000:
                        abort(400, 'Слишком длинный текст')
                text_fields = {'items':['name','from','to','body','cargo','date','vat'], 'partners':['name','inn','phone','contact'], 'deals':['name','status'], 'calls':['name','date','time','topic','phone'], 'notes':['text','date','partner'], 'templates':['name','text','type']}[key]
                for field in text_fields:
                    if not isinstance(row.get(field,''),str): abort(400, 'Текстовое поле имеет неверный формат')
                    row.setdefault(field, '')
                numeric_fields = {'items': ['weight','rate'], 'deals': ['client','carrier']}.get(key, [])
                for field in numeric_fields:
                    try:
                        number = float(row.get(field) or 0)
                    except (ValueError, TypeError):
                        abort(400, 'Ставка и вес должны быть числами')
                    if not math.isfinite(number) or number < 0:
                        abort(400, 'Ставка и вес должны быть неотрицательными числами')
                if key == 'notes' and not row.get('text'):
                    abort(400, 'Заметка не может быть пустой')
                if key in ('items','partners','deals','calls','templates') and not row.get('name'): abort(400, 'Запись без названия')
                if key == 'deals' and row.get('status') not in ('Новая','Поиск машины','Согласование','В пути','Документы','Закрыта'): abort(400)
                if key == 'items' and (row.get('kind') not in ('cargo','truck') or not row.get('from') or not row.get('to')): abort(400)
                if key == 'calls' and type(row.get('done',False)) is not bool: abort(400)

    @app.put('/api/state')
    def update_state():
        payload = request.get_json() or {}
        data, revision = payload.get('data'), payload.get('revision')
        validate_state(data)
        if type(revision) is not int:
            abort(400)
        with db() as q:
            result = q('UPDATE workspace SET data=?,revision=revision+1 WHERE id=1 AND revision=?',
                       (json.dumps(data, ensure_ascii=False, allow_nan=False), revision))
            if not result.rowcount:
                abort(409, 'База изменена другим сотрудником. Обнови данные и повтори изменение')
            audit(q, g.user['login'], 'Изменение базы', str(revision+1))
        return jsonify(revision=revision+1)

    def admin():
        if g.user['role'] not in ('owner','admin'):
            abort(403, 'Доступно только администратору')

    @app.post('/api/import/excel')
    def import_excel():
        admin()
        upload = request.files.get('file')
        if not upload or not upload.filename.lower().endswith('.xlsx'):
            abort(400, 'Выбери файл Excel в формате .xlsx (до 5 МБ)')
        mode = request.form.get('mode', 'preview')
        if mode not in ('preview', 'commit'):
            abort(400, 'Неизвестный режим импорта')
        try:
            rows = read_contacts(upload.stream)
        except Exception as exc:
            abort(400, str(exc) if isinstance(exc, ValueError) else 'Не удалось прочитать Excel-файл')
        with db() as q:
            row = q('SELECT revision,data FROM workspace WHERE id=1').fetchone()
            if mode == 'commit' and request.form.get('revision') != str(row['revision']):
                abort(409, 'База изменилась. Повтори предварительный просмотр импорта')
            data = json.loads(row['data'])
            try:
                result = merge_contacts(data, rows)
            except ValueError as exc:
                abort(400, str(exc))
            validate_state(data)
            revision = row['revision']
            if mode == 'commit' and result['added']:
                updated = q('UPDATE workspace SET data=?,revision=revision+1 WHERE id=1 AND revision=?',
                            (json.dumps(data, ensure_ascii=False, allow_nan=False), revision))
                if not updated.rowcount:
                    abort(409, 'База изменилась. Повтори предварительный просмотр импорта')
                revision += 1
                audit(q, g.user['login'], 'Импорт Excel',
                      f"Добавлено: {result['added']}; пропущено: {result['skipped']}")
        return jsonify(**result, revision=revision)

    @app.get('/api/users')
    def users():
        admin()
        with db() as q:
            rows = q('SELECT * FROM users ORDER BY login').fetchall()
        return jsonify(users=[public_user(u) for u in rows])

    @app.post('/api/users')
    def add_user():
        admin()
        data = request.get_json() or {}
        login, display = valid_login(data.get('login')), name(data.get('name'))
        role = data.get('role','member')
        if role not in ('admin','member') or (role=='admin' and g.user['role']!='owner'):
            abort(403, 'Только главный администратор назначает администраторов')
        password = strong_password(data.get('password'))
        with db() as q:
            # Serialize account management operations.
            q('UPDATE users SET active=active WHERE id=?', (g.user['id'],))
            if q('SELECT id FROM users WHERE login=?',(login,)).fetchone():
                abort(409, 'Логин уже занят')
            q('INSERT INTO users VALUES (?,?,?,?,?,?,?)', (str(uuid.uuid4()),login,display,generate_password_hash(password),role,1,1))
            audit(q,g.user['login'],'Создание пользователя',login)
        return jsonify(ok=True),201

    @app.patch('/api/users/<uid>')
    def edit_user(uid):
        admin()
        data = request.get_json() or {}
        with db() as q:
            target = q('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not target:
                abort(404)
            if target['role']=='owner' or target['id']==g.user['id']:
                abort(403, 'Главного администратора нельзя отключить или изменить здесь')
            if g.user['role']!='owner' and (target['role']!='member' or 'role' in data):
                abort(403)
            role = data.get('role',target['role'])
            if role not in ('admin','member'):
                abort(400)
            active = data.get('active',bool(target['active']))
            if type(active) is not bool:
                abort(400)
            password = target['password']
            must_change = target['must_change']
            if data.get('password'):
                password = generate_password_hash(strong_password(data['password']))
                must_change = 1
            q('UPDATE users SET role=?,active=?,password=?,must_change=? WHERE id=?',(role,int(active),password,must_change,uid))
            q('DELETE FROM sessions WHERE user_id=?',(uid,))
            audit(q,g.user['login'],'Изменение доступа',target['login'])
        return jsonify(ok=True)

    @app.get('/api/audit')
    def log():
        admin()
        with db() as q:
            rows=q('SELECT * FROM audit ORDER BY created DESC LIMIT 200').fetchall()
        return jsonify(events=[dict(r) for r in rows])

    return app

DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))

if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.getenv('PORT','8080')))
