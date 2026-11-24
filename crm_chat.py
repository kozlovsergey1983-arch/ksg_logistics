"""Authenticated chat shared by web and mobile clients."""
import io
import json
import re
import time
import uuid
from pathlib import PurePath

from flask import abort, g, jsonify, request, send_file, session

CHAT_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS chat_messages (id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_name TEXT NOT NULL, recipient TEXT NOT NULL, body TEXT NOT NULL, created BIGINT NOT NULL)",
    'CREATE INDEX IF NOT EXISTS chat_conversation ON chat_messages (recipient,sender,created)',
    'CREATE TABLE IF NOT EXISTS chat_presence (user_id TEXT PRIMARY KEY, seen BIGINT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS chat_reads (user_id TEXT NOT NULL, message_id TEXT NOT NULL REFERENCES chat_messages(id), PRIMARY KEY(user_id,message_id))',
    'CREATE TABLE IF NOT EXISTS chat_cards (message_id TEXT PRIMARY KEY REFERENCES chat_messages(id), kind TEXT NOT NULL, card_id TEXT NOT NULL, title TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS chat_files (message_id TEXT PRIMARY KEY REFERENCES chat_messages(id), name TEXT NOT NULL, content BYTEA NOT NULL)',
]


def register_chat(app, db):
    def touch(q):
        q('INSERT INTO chat_presence VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET seen=excluded.seen',
          (g.user['id'], int(time.time())))

    @app.get('/api/chat/users')
    def chat_users():
        with db() as q:
            touch(q)
            rows = q('SELECT u.id,u.name,p.seen FROM users u LEFT JOIN chat_presence p ON p.user_id=u.id WHERE u.active=1 ORDER BY u.name').fetchall()
        return jsonify(users=[dict(id=u['id'], name=u['name'], online=(u['seen'] or 0) >= time.time()-60) for u in rows])

    @app.get('/api/chat/unread')
    def unread():
        me = g.user['id']
        with db() as q:
            touch(q)
            rows = q("""SELECT CASE WHEN m.recipient='' THEN '' ELSE m.sender END AS peer, COUNT(*) AS count
                FROM chat_messages m LEFT JOIN chat_reads r ON r.message_id=m.id AND r.user_id=?
                WHERE m.recipient IN ('',?) AND m.sender<>? AND r.user_id IS NULL
                GROUP BY CASE WHEN m.recipient='' THEN '' ELSE m.sender END""", (me, me, me)).fetchall()
        return jsonify(conversations=[dict(row) for row in rows])

    @app.get('/api/chat')
    def chat_read():
        peer = request.args.get('peer', '')
        me = g.user['id']
        scope = '((m.sender=? AND m.recipient=?) OR (m.sender=? AND m.recipient=?))' if peer else "m.recipient=''"
        params = (me, peer, peer, me) if peer else ()
        before = request.args.get('before', '')
        if before:
            match = re.fullmatch(r'([0-9]{1,16}):([A-Za-z0-9_-]{1,100})', before)
            if not match:
                abort(400, 'Неверная позиция в истории чата')
            created, mid = int(match[1]), match[2]
            scope += ' AND (m.created<? OR (m.created=? AND m.id<?))'
            params += (created, created, mid)
        with db() as q:
            rows = q(f"""SELECT m.*, f.name AS filename, c.kind, c.card_id, c.title,
                (SELECT COUNT(*) FROM chat_reads r WHERE r.message_id=m.id) AS read_count,
                (SELECT COUNT(*) FROM chat_reads r WHERE r.message_id=m.id AND r.user_id=?) AS read_by_me
                FROM chat_messages m LEFT JOIN chat_files f ON f.message_id=m.id
                LEFT JOIN chat_cards c ON c.message_id=m.id WHERE {scope}
                ORDER BY m.created DESC,m.id DESC LIMIT 201""", (me, *params)).fetchall()
        messages = []
        for row in reversed(rows[:200]):
            message = dict(row)
            if session.get('mobile_member'):
                message.pop('card_id', None)
            messages.append(message)
        cursor = f"{messages[0]['created']}:{messages[0]['id']}" if len(rows) > 200 else ''
        return jsonify(messages=messages, before=cursor)

    @app.post('/api/chat/read')
    def mark_read():
        me = g.user['id']
        ids = request.json.get('ids')
        if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(i, str) or len(i) > 36 for i in ids):
            abort(400)
        with db() as q:
            for mid in sorted(set(ids)):
                q("""INSERT INTO chat_reads SELECT ?,id FROM chat_messages
                     WHERE id=? AND recipient IN ('',?) AND sender<>?
                     ON CONFLICT(user_id,message_id) DO NOTHING""", (me, mid, me, me))
        return jsonify(ok=True)

    @app.post('/api/chat')
    def chat_send():
        data = request.form if request.mimetype == 'multipart/form-data' else request.json
        peer, body = data.get('peer', ''), data.get('text', '')
        kind, card_id = data.get('kind', ''), data.get('card_id', '')
        if not all(isinstance(value, str) for value in (peer, body, kind, card_id)) or len(body) > 10000:
            abort(400, 'Неверное сообщение')
        file = request.files.get('file')
        if not (body.strip() or card_id or file):
            abort(400, 'Сообщение пустое')
        mid = str(uuid.uuid4())
        with db() as q:
            if peer and not q('SELECT id FROM users WHERE id=? AND active=1', (peer,)).fetchone():
                abort(404, 'Сотрудник не найден')
            q('INSERT INTO chat_messages VALUES (?,?,?,?,?,?)',
              (mid, g.user['id'], g.user['name'], peer, body.strip(), int(time.time()*1000)))
            if card_id:
                if session.get('mobile_member'):
                    abort(403)
                if kind not in ('deals', 'partners'):
                    abort(400, 'Выбери сделку или контрагента')
                row = q('SELECT data FROM workspace WHERE id=1').fetchone()
                card = next((v for v in json.loads(row['data'])[kind] if v['id'] == card_id), None)
                if not card:
                    abort(404, 'Карточка не найдена')
                q('INSERT INTO chat_cards VALUES (?,?,?,?)', (mid, kind, card_id, card['name']))
            if file:
                name = PurePath(file.filename.replace(chr(92), '/')).name
                content = file.read()
                if not content or not name or len(name) > 180 or any(ord(c) < 32 for c in name):
                    abort(400, 'Неверный файл')
                q('INSERT INTO chat_files VALUES (?,?,?)', (mid, name, content))
        return jsonify(ok=True, id=mid), 201

    @app.get('/api/chat/files/<mid>')
    def attachment(mid):
        me = g.user['id']
        with db() as q:
            row = q("""SELECT f.name,f.content FROM chat_files f JOIN chat_messages m ON m.id=f.message_id
                    WHERE m.id=? AND (m.recipient IN ('',?) OR m.sender=?)""", (mid, me, me)).fetchone()
        if not row:
            abort(404)
        content = bytes(row['content'])
        mimetype = 'application/octet-stream'
        preview = request.args.get('preview') == '1'
        if preview:
            if content.startswith(b'\x89PNG\r\n\x1a\n'):
                mimetype = 'image/png'
            elif content.startswith(b'\xff\xd8\xff'):
                mimetype = 'image/jpeg'
            elif content.startswith((b'GIF87a', b'GIF89a')):
                mimetype = 'image/gif'
            elif content.startswith(b'RIFF') and content[8:12] == b'WEBP':
                mimetype = 'image/webp'
            else:
                abort(404)
        return send_file(io.BytesIO(content), mimetype=mimetype, as_attachment=not preview, download_name=row['name'])
