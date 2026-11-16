"""Shared document library. Bytes live in the database and survive deployments."""
import base64
from io import BytesIO
import re
import time
import uuid

from flask import abort, g, jsonify, request, send_file

MAX_FILE_BYTES = 4 * 1024 * 1024
DOCUMENT_SCHEMA = [
    'CREATE TABLE IF NOT EXISTS document_state (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)',
    'INSERT INTO document_state VALUES (1,0) ON CONFLICT(id) DO NOTHING',
    'CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, name TEXT NOT NULL, size BIGINT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, position INTEGER NOT NULL, uploader_id TEXT NOT NULL REFERENCES users(id), uploader_name TEXT NOT NULL, uploaded BIGINT NOT NULL, reason TEXT NOT NULL)',
]


def register_documents(app, db, audit, admin):
    def is_admin():
        return g.user['role'] in ('owner', 'admin')

    def claim_revision(q, revision):
        if type(revision) is not int:
            abort(400, 'Неверная версия списка документов')
        result = q('UPDATE document_state SET revision=revision+1 WHERE id=1 AND revision=?', (revision,))
        if not result.rowcount:
            abort(409, 'Список документов изменился. Обнови его и повтори действие')

    def next_position(q):
        row = q("SELECT MAX(position) AS value FROM documents WHERE status='approved'").fetchone()
        return (row['value'] if row['value'] is not None else -1) + 1

    @app.get('/api/documents')
    def documents_list():
        # One query obtains both revision and visible metadata from the same snapshot.
        with db() as q:
            rows = q('SELECT s.revision,d.id,d.name,d.size,d.status,d.position,d.uploader_name,d.uploaded,d.reason '
                     'FROM document_state s LEFT JOIN documents d ON '
                     "(d.status='approved' OR ?=1 OR d.uploader_id=?) "
                     'WHERE s.id=1 ORDER BY d.position,d.uploaded,d.id',
                     (int(is_admin()), g.user['id'])).fetchall()
        return jsonify(revision=rows[0]['revision'], documents=[
            {k: row[k] for k in ('id', 'name', 'size', 'status', 'position', 'uploader_name', 'uploaded', 'reason')}
            for row in rows if row['id'] is not None])

    @app.post('/api/documents')
    def documents_upload():
        upload = request.files.get('file')
        if not upload or not upload.filename:
            abort(400, 'Выбери файл')
        filename = re.sub(r'[\x00-\x1f\x7f]', '', upload.filename.replace('\\', '/').rsplit('/', 1)[-1]).strip()
        if not filename or filename in ('.', '..') or len(filename) > 200:
            abort(400, 'Имя файла должно содержать от 1 до 200 символов')
        content = upload.stream.read(MAX_FILE_BYTES + 1)
        if not content or len(content) > MAX_FILE_BYTES:
            abort(400, 'Размер файла: от 1 байта до 4 МБ')
        try:
            revision = int(request.form.get('revision', ''))
        except ValueError:
            abort(400, 'Обнови список документов перед загрузкой')
        status = 'approved' if is_admin() else 'pending'
        did = str(uuid.uuid4())
        with db() as q:
            claim_revision(q, revision)
            position = next_position(q) if status == 'approved' else 0
            q('INSERT INTO documents (id,name,size,content,status,position,uploader_id,uploader_name,uploaded,reason) VALUES (?,?,?,?,?,?,?,?,?,?)',
              (did, filename, len(content), base64.b64encode(content).decode('ascii'), status,
               position, g.user['id'], g.user['name'], int(time.time()), ''))
            audit(q, g.user['login'], 'Публикация документа' if status == 'approved' else 'Документ на подтверждение', filename)
        return jsonify(id=did, status=status, revision=revision+1), 201

    @app.get('/api/documents/<did>/download')
    def documents_download(did):
        with db() as q:
            doc = q('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
        if not doc or (doc['status'] != 'approved' and not is_admin() and doc['uploader_id'] != g.user['id']):
            abort(404, 'Документ не найден')
        return send_file(BytesIO(base64.b64decode(doc['content'])), mimetype='application/octet-stream',
                         as_attachment=True, download_name=doc['name'])

    @app.post('/api/documents/<did>/moderate')
    def documents_moderate(did):
        admin()
        payload = request.get_json()
        action, revision = payload.get('action'), payload.get('revision')
        reason = payload.get('reason', '')
        if action not in ('approve', 'reject') or not isinstance(reason, str) or len(reason) > 1000:
            abort(400, 'Неверное действие или слишком длинная причина')
        with db() as q:
            claim_revision(q, revision)
            doc = q('SELECT id,name,status FROM documents WHERE id=?', (did,)).fetchone()
            if not doc:
                abort(404, 'Документ не найден')
            if doc['status'] != 'pending':
                abort(409, 'Документ уже рассмотрен. Обнови список')
            status = 'approved' if action == 'approve' else 'rejected'
            position = next_position(q) if status == 'approved' else 0
            q('UPDATE documents SET status=?,position=?,reason=? WHERE id=?',
              (status, position, reason.strip() if status == 'rejected' else '', did))
            audit(q, g.user['login'], 'Подтверждение документа' if action == 'approve' else 'Отклонение документа', doc['name'])
        return jsonify(revision=revision+1)

    @app.delete('/api/documents/<did>')
    def documents_delete(did):
        admin()
        revision = request.get_json().get('revision')
        with db() as q:
            claim_revision(q, revision)
            doc = q('SELECT name FROM documents WHERE id=?', (did,)).fetchone()
            if not doc:
                abort(404, 'Документ не найден')
            q('DELETE FROM documents WHERE id=?', (did,))
            audit(q, g.user['login'], 'Удаление документа', doc['name'])
        return jsonify(revision=revision+1)

    @app.put('/api/documents/order')
    def documents_order():
        admin()
        payload = request.get_json()
        ids, revision = payload.get('ids'), payload.get('revision')
        if not isinstance(ids, list) or len(ids) > 10000 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            abort(400, 'Неверный порядок документов')
        with db() as q:
            claim_revision(q, revision)
            existing = {d['id'] for d in q("SELECT id FROM documents WHERE status='approved'").fetchall()}
            if set(ids) != existing:
                abort(409, 'Список опубликованных документов изменился. Обнови его')
            for position, did in enumerate(ids):
                q('UPDATE documents SET position=? WHERE id=?', (position, did))
            audit(q, g.user['login'], 'Изменение порядка документов', str(len(ids)))
        return jsonify(revision=revision+1)
