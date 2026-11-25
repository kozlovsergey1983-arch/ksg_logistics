"""Contract templates, immutable generated files, and the status register."""
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from flask import abort, g, jsonify, request, send_file, session
from lxml import etree

from crm_workspace import MOSCOW

CONTRACT_SCHEMA = [
    'CREATE TABLE IF NOT EXISTS contract_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, content BYTEA NOT NULL)',
    'CREATE TABLE IF NOT EXISTS contract_sequence (year INTEGER PRIMARY KEY, value INTEGER NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS contracts (
       id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL, partner TEXT NOT NULL, deal TEXT NOT NULL,
       status TEXT NOT NULL, created TEXT NOT NULL, snapshot TEXT NOT NULL, content BYTEA NOT NULL)''',
    'CREATE TABLE IF NOT EXISTS contract_pdfs (id TEXT PRIMARY KEY REFERENCES contracts(id), content BYTEA NOT NULL)',
]
STATUSES = ('Черновик', 'На согласовании', 'Подписан', 'Аннулирован')
FIELDS = ('name', 'inn', 'kpp', 'ogrn', 'address', 'bank', 'bik', 'account', 'correspondentAccount')
TOKEN = re.compile(r'\{\{\s*([A-Za-z_.]+)\s*\}\}')
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def open_template(content):
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(e.file_size for e in entries) > 20 * 1024 * 1024:
                raise ValueError
            for entry in entries:
                path = entry.filename.lower()
                if any(x in path for x in ('vbaproject', '/embeddings/', '/activex/')):
                    raise ValueError
                if path.endswith(('.xml', '.rels')):
                    raw = archive.read(entry)
                    if re.search(rb'<!\s*(?:DOCTYPE|ENTITY)', raw, re.I) or b'\x00' in raw:
                        raise ValueError
                    root = etree.fromstring(raw)
                    if path.endswith('.rels') and any(e.get('TargetMode') == 'External' for e in root):
                        raise ValueError
                    if any(etree.QName(e).localname in ('instrText', 'fldSimple', 'altChunk', 'object')
                           for e in root.iter() if isinstance(e.tag, str)):
                        raise ValueError
        return Document(io.BytesIO(content))
    except (BadZipFile, KeyError, ValueError, OSError, RuntimeError, etree.XMLSyntaxError):
        abort(400, 'Нужен DOCX без макросов, внешних ссылок и активных полей (до 20 МБ после распаковки)')


def render_template(doc, values):
    roots = [doc.element]
    roots.extend(part.element for part in doc.part.package.parts
                 if part.content_type.endswith(('.header+xml', '.footer+xml')))
    for root in roots:
        for paragraph in root.xpath('.//w:p'):
            nodes = paragraph.xpath('./w:r/w:t | ./w:hyperlink/w:r/w:t')
            text = ''.join(node.text or '' for node in nodes)
            if '{{' in TOKEN.sub('', text) or '}}' in TOKEN.sub('', text):
                abort(400, 'Проверь формат подстановок в шаблоне Word')
            for match in reversed(list(TOKEN.finditer(text))):
                if match[1] not in values:
                    abort(400, f'Неизвестное поле шаблона: {match[1]}')
                position = 0
                for node in nodes:
                    original = node.text or ''
                    end = position + len(original)
                    if position < match.end() and end > match.start():
                        replacement = values[match[1]] if position <= match.start() < end else ''
                        node.text = original[:max(0, match.start()-position)] + replacement + original[max(0, match.end()-position):]
                        node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                    position = end
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def pdf_bytes(content):
    executable = shutil.which('libreoffice')
    if not executable:
        abort(503, 'Для экспорта в PDF нужен LibreOffice на сервере')
    with tempfile.TemporaryDirectory(prefix='ksg-contract-') as directory:
        root = Path(directory)
        source = root / 'contract.docx'
        source.write_bytes(content)
        try:
            subprocess.run([executable, '-env:UserInstallation=' + (root / 'profile').as_uri(),
                            '--headless', '--convert-to', 'pdf:writer_pdf_Export', '--outdir', directory, str(source)],
                           check=True, timeout=25, capture_output=True,
                           env={'PATH': os.environ.get('PATH', ''), 'HOME': directory, 'LANG': 'C.UTF-8'})
            return (root / 'contract.pdf').read_bytes()
        except (subprocess.SubprocessError, OSError):
            abort(502, 'Не удалось преобразовать договор в PDF')


def register_contracts(app, db, admin, audit):
    @app.before_request
    def require_workspace_access():
        if request.path.startswith('/api/contracts') and session.get('mobile_member'):
            abort(403)

    @app.get('/api/contracts/templates')
    def templates():
        with db() as q:
            rows = q('SELECT id,name FROM contract_templates ORDER BY name,id').fetchall()
        return jsonify(templates=[dict(row) for row in rows])

    @app.post('/api/contracts/templates')
    def upload_template():
        admin()
        upload = request.files.get('file')
        name = request.form.get('name', '').strip()
        if not upload or not upload.filename.lower().endswith('.docx') or not 1 <= len(name) <= 150:
            abort(400, 'Укажи название и выбери шаблон DOCX')
        content = upload.read()
        open_template(content)
        tid = str(uuid.uuid4())
        with db() as q:
            q('INSERT INTO contract_templates VALUES (?,?,?)', (tid, name, content))
            audit(q, g.user['login'], 'Загрузка шаблона договора', name)
        return jsonify(id=tid), 201

    @app.get('/api/contracts')
    def registry():
        partner, deal = request.args.get('partner', ''), request.args.get('deal', '')
        with db() as q:
            rows = q("""SELECT c.id,c.number,c.partner,c.deal,c.status,c.created,c.snapshot,d.message_id,d.entity_id
                         FROM contracts c LEFT JOIN diadoc_delivery d ON d.contract_id=c.id
                         WHERE (?='' OR c.partner=?) AND (?='' OR c.deal=?) ORDER BY c.created DESC, c.number DESC""",
                     (partner, partner, deal, deal)).fetchall()
        result = []
        for row in rows:
            contract = dict(row, snapshot=json.loads(row['snapshot']))
            if g.user['role'] == 'member':
                contract.pop('message_id', None)
                contract.pop('entity_id', None)
            result.append(contract)
        return jsonify(contracts=result)

    @app.post('/api/contracts')
    def generate():
        data = request.json
        template, partner, deal = (data.get(k, '') for k in ('template', 'partner', 'deal'))
        if any(not isinstance(x, str) or len(x) > 100 for x in (template, partner, deal)):
            abort(400)
        terms = {k: data.get(k, '') for k in ('route', 'rate', 'cargo', 'payment')}
        if any(not isinstance(x, str) or len(x) > 2000 or re.search(r'[\x00-\x1f]', x) for x in terms.values()):
            abort(400, 'Проверь условия договора')
        with db() as q:
            tpl = q('SELECT content FROM contract_templates WHERE id=?', (template,)).fetchone()
            workspace = json.loads(q('SELECT data FROM workspace WHERE id=1').fetchone()['data'])
            counterparty = next((p for p in workspace['partners'] if p['id'] == partner), None)
            linked_deal = next((d for d in workspace['deals'] if d['id'] == deal), None)
            if not tpl or not counterparty or (deal and not linked_deal):
                abort(404, 'Шаблон, контрагент или сделка не найдены')
            if counterparty.get('archived') or counterparty.get('deletionRequested'):
                abort(409, 'Контрагент находится в архиве')
            if linked_deal and linked_deal.get('partnerId') and linked_deal['partnerId'] != partner:
                abort(400, 'Выбери контрагента сделки')
            company = app.config['CONTRACT_COMPANY']
            if not str(counterparty.get('inn') or '').strip():
                abort(409, 'Укажи ИНН в карточке контрагента')
            if not str(company.get('name') or '').strip() or not str(company.get('inn') or '').strip():
                abort(409, 'Укажи название и ИНН своей организации в CONTRACT_COMPANY_JSON')
            now = datetime.now(MOSCOW)
            sequence = q('''INSERT INTO contract_sequence VALUES (?,1)
                          ON CONFLICT(year) DO UPDATE SET value=contract_sequence.value+1 RETURNING value''',
                         (now.year,)).fetchone()['value']
            number = f'{now.year}-{sequence:06}'
            values = dict(terms, number=number, date=now.date().isoformat())
            for prefix, party in (('company', company), ('partner', counterparty)):
                values.update({f'{prefix}.{k}': str(party.get(k) or '') for k in FIELDS})
            content = render_template(open_template(bytes(tpl['content'])), values)
            cid = str(uuid.uuid4())
            q('INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?)',
              (cid, number, partner, deal, STATUSES[0], now.isoformat(), json.dumps(values, ensure_ascii=False), content))
            audit(q, g.user['login'], 'Создание договора', number)
        return jsonify(id=cid, number=number), 201

    @app.patch('/api/contracts/<cid>')
    def update_status(cid):
        admin()
        status, expected = request.json.get('status'), request.json.get('expected')
        if status not in STATUSES or expected not in STATUSES:
            abort(400)
        with db() as q:
            if not q('UPDATE contracts SET status=? WHERE id=? AND status=?', (status, cid, expected)).rowcount:
                abort(409, 'Статус изменился. Обнови реестр')
            audit(q, g.user['login'], 'Статус договора', cid + ' · ' + status)
        return jsonify(ok=True)

    @app.get('/api/contracts/<cid>/<format>')
    def download(cid, format):
        if format not in ('docx', 'pdf'):
            abort(404)
        with db() as q:
            row = q('SELECT number,content FROM contracts WHERE id=?', (cid,)).fetchone()
            if not row:
                abort(404)
            cached = q('SELECT content FROM contract_pdfs WHERE id=?', (cid,)).fetchone() if format == 'pdf' else None
        content = bytes(row['content'])
        if format == 'pdf':
            content = bytes(cached['content']) if cached else pdf_bytes(content)
            if not cached:
                with db() as q:
                    q('INSERT INTO contract_pdfs VALUES (?,?) ON CONFLICT(id) DO NOTHING', (cid, content))
                    stored = q('SELECT content FROM contract_pdfs WHERE id=?', (cid,)).fetchone()
                    content = bytes(stored['content'])
        return send_file(io.BytesIO(content), as_attachment=True, mimetype=DOCX if format == 'docx' else 'application/pdf',
                         download_name=f"{row['number']}.{format}")
