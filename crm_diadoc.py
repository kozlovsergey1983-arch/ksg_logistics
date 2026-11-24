"""Opt-in Diadoc adapter. Private keys and signature creation stay with the signer."""
import base64
import binascii
import hashlib
import io
import json
import os
import re
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from flask import abort, g, jsonify, request, send_file

DIADOC_SCHEMA = [
    '''CREATE TABLE IF NOT EXISTS diadoc_attempts (
       contract_id TEXT PRIMARY KEY REFERENCES contracts(id), recipient TEXT NOT NULL, signature_hash TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS diadoc_delivery (
       contract_id TEXT PRIMARY KEY REFERENCES contracts(id), recipient TEXT NOT NULL,
       message_id TEXT NOT NULL, entity_id TEXT NOT NULL)''',
]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def transport(path, params, token, payload=None, binary=False):
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/octet-stream' if binary else 'application/json'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json; charset=utf-8'
    url = 'https://diadoc-api.kontur.ru' + path + '?' + urlencode(params)
    try:
        with build_opener(NoRedirect()).open(Request(url, data=data, headers=headers), timeout=12) as response:
            raw = response.read(12 * 1024 * 1024 + 1)
            if len(raw) > 12 * 1024 * 1024:
                abort(502, 'Диадок вернул слишком большой ответ')
            return (raw if binary else json.loads(raw) if raw else {}), response.status
    except HTTPError as exc:
        if exc.code == 404:
            abort(404, 'Объект не найден в Диадоке')
        if exc.code in (401, 402, 403):
            abort(503, 'Проверь доступ к API, тариф и права в ящике Диадока')
        if exc.code == 429:
            abort(503, 'Диадок ограничил частоту запросов')
        if exc.code in (400, 409):
            abort(422, 'Диадок отклонил операцию. Проверь реквизиты, статус и подпись')
        abort(502, 'Диадок временно недоступен')
    except (URLError, OSError, ValueError):
        abort(502, 'Не удалось получить ответ Диадока')


def register_diadoc(app, db, admin, audit):
    def config():
        return app.config.get('DIADOC') or {}

    def call(path, params=None, payload=None, *, binary=False):
        settings = config()
        if not settings.get('token') or not settings.get('box'):
            abort(503, 'Диадок ещё не подключён')
        send = app.config.get('DIADOC_TRANSPORT', transport)
        return send(path, params or {}, settings['token'], payload, binary)

    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9@._-]{1,160}', value):
            abort(400, 'Неверный идентификатор Диадока')
        return value

    @app.get('/api/diadoc')
    def settings():
        admin()
        current = config()
        return jsonify(enabled=bool(current.get('box') and current.get('token')),
                       signer=g.user['login'] == current.get('signer'))

    @app.get('/api/diadoc/organizations')
    def organizations():
        admin()
        inn, kpp = request.args.get('inn', ''), request.args.get('kpp', '')
        if not re.fullmatch(r'(?:[0-9]{10}|[0-9]{12})', inn) or (kpp and not re.fullmatch(r'[0-9]{9}', kpp)):
            abort(400, 'Укажи ИНН и, при необходимости, КПП')
        params = dict(inn=inn)
        if kpp:
            params['kpp'] = kpp
        data, code = call('/GetOrganizationsByInnKpp', params)
        return jsonify(data), code

    @app.post('/api/diadoc/invitations')
    def invite():
        admin()
        data = request.json
        peer = identifier(data.get('box'))
        message = data.get('message', '')
        if not isinstance(message, str) or len(message) > 1000 or peer == config().get('box'):
            abort(400)
        result, code = call('/V3/AcquireCounteragent', {'myBoxId': config().get('box')},
                            {'BoxId': peer, 'MessageToCounteragent': message})
        with db() as q:
            audit(q, g.user['login'], 'Приглашение Диадок', peer)
        return jsonify(result), code

    @app.get('/api/diadoc/invitations/<task>')
    def invitation(task):
        admin()
        data, code = call('/V3/AcquireCounteragentResult', {'myBoxId': config().get('box'), 'taskId': identifier(task)})
        if code == 204:
            return jsonify(status='pending'), 202
        return jsonify(data), code

    @app.get('/api/diadoc/documents')
    def documents():
        admin()
        direction = request.args.get('direction', 'Inbound')
        if direction not in ('Inbound', 'Outbound'):
            abort(400)
        params = {'boxId': config().get('box'), 'filterCategory': 'Any.' + direction}
        cursor = request.args.get('after', '')
        if len(cursor) > 2000:
            abort(400)
        if cursor:
            params['afterIndexKey'] = cursor
        data, code = call('/V3/GetDocuments', params)
        return jsonify(data), code

    @app.get('/api/diadoc/documents/<mid>/<eid>')
    def document(mid, eid):
        admin()
        data, code = call('/V3/GetDocument', {'boxId': config().get('box'), 'messageId': identifier(mid),
                                              'entityId': identifier(eid)})
        return jsonify(data), code

    @app.get('/api/diadoc/documents/<mid>/<eid>/file')
    def document_file(mid, eid):
        admin()
        content, code = call('/V4/GetEntityContent', {'boxId': config().get('box'), 'messageId': identifier(mid),
                                                      'entityId': identifier(eid)}, binary=True)
        if code != 200:
            abort(502, 'Файл пока недоступен')
        return send_file(io.BytesIO(content), as_attachment=True, download_name='diadoc-document',
                         mimetype='application/octet-stream')

    @app.post('/api/diadoc/contracts/<cid>')
    def send_contract(cid):
        admin()
        if not config().get('signer') or config()['signer'] != g.user['login']:
            abort(403, 'Отправка доступна назначенному подписанту')
        data = request.json
        peer = identifier(data.get('box'))
        encoded = data.get('signature', '')
        if peer == config().get('box') or not isinstance(encoded, str) or not 1 <= len(encoded) <= 256000:
            abort(400, 'Нужна отделённая подпись и ящик получателя')
        try:
            signature = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            abort(400, 'Подпись должна быть в формате Base64')
        with db() as q:
            row = q('SELECT c.*,p.content AS pdf FROM contracts c LEFT JOIN contract_pdfs p ON p.id=c.id WHERE c.id=?',
                    (cid,)).fetchone()
            prior = q('SELECT recipient,message_id,entity_id FROM diadoc_delivery WHERE contract_id=?', (cid,)).fetchone()
        if not row:
            abort(404)
        if prior:
            if prior['recipient'] != peer:
                abort(409, 'Договор уже отправлен другому получателю')
            return jsonify(dict(prior))
        if not row['pdf'] or row['status'] == 'Аннулирован':
            abort(409, 'Скачай PDF из реестра перед подписанием. Аннулированный договор отправить нельзя')
        snapshot = json.loads(row['snapshot'])
        params = {'inn': re.sub(r'\s+', '', snapshot['partner.inn'])}
        if snapshot.get('partner.kpp'):
            params['kpp'] = snapshot['partner.kpp']
        lookup, _ = call('/GetOrganizationsByInnKpp', params)
        permitted = {box.get('BoxId') for org in lookup.get('Organizations', [])
                     for box in org.get('Boxes', [])}
        if peer not in permitted:
            abort(400, 'Ящик получателя не соответствует ИНН контрагента')
        signature_hash = hashlib.sha256(signature).hexdigest()
        with db() as q:
            q('INSERT INTO diadoc_attempts VALUES (?,?,?) ON CONFLICT(contract_id) DO NOTHING',
              (cid, peer, signature_hash))
            attempt = q('SELECT recipient,signature_hash FROM diadoc_attempts WHERE contract_id=?', (cid,)).fetchone()
            if (attempt['recipient'], attempt['signature_hash']) != (peer, signature_hash):
                abort(409, 'По договору уже начата отправка. Для повтора используй тот же ящик и файл подписи')
        metadata = {'FileName': row['number'] + '.pdf', 'DocumentNumber': row['number'],
                    'DocumentDate': date.fromisoformat(row['created'][:10]).strftime('%d.%m.%Y')}
        payload = {'FromBoxId': config()['box'], 'ToBoxId': peer, 'DocumentAttachments': [{
            'TypeNamedId': 'Contract', 'NeedRecipientSignature': True,
            'Metadata': [{'Key': k, 'Value': v} for k, v in metadata.items()],
            'SignedContent': {'Content': base64.b64encode(bytes(row['pdf'])).decode(),
                              'Signature': base64.b64encode(signature).decode()},
        }]}
        response, _ = call('/V3/PostMessage', {'operationId': cid}, payload)
        mid = response.get('MessageId')
        eid = next((e.get('EntityId') for e in response.get('Entities', []) if e.get('EntityType') == 'Attachment'), None)
        valid = lambda value: isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9@._-]{1,160}', value))
        if not valid(mid) or not valid(eid) or response.get('ToBoxId') != peer:
            abort(502, 'Диадок вернул неполный результат. Проверь исходящие перед повтором')
        with db() as q:
            inserted = q('INSERT INTO diadoc_delivery VALUES (?,?,?,?) ON CONFLICT(contract_id) DO NOTHING',
                         (cid, peer, mid, eid)).rowcount
            if inserted:
                audit(q, g.user['login'], 'Отправка договора через Диадок', row['number'])
        return jsonify(recipient=peer, message_id=mid, entity_id=eid), 201


def environment_settings():
    return {'token': os.getenv('DIADOC_ACCESS_TOKEN', ''), 'box': os.getenv('DIADOC_BOX_ID', ''),
            'signer': os.getenv('DIADOC_SIGNER_LOGIN', '')}
