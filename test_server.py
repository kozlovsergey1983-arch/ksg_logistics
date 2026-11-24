import json
import os
import tempfile
import unittest
from server import create_app

class CRMTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, APP_ENV='test', SECRET_KEY='test-only-secret-'*4, SQLITE_PATH=self.tmp.name+'/test.sqlite', DATABASE_URL=None, ADMIN_PASSWORD='owner-initial-test-password')
        self.app = create_app(self.config)
        self.owner = self.app.test_client()
        self.login(self.owner,'admin','owner-initial-test-password')
        self.post(self.owner,'/api/password',dict(old='owner-initial-test-password',password='owner-changed-test-password'))
        self.login(self.owner,'admin','owner-changed-test-password')
    def tearDown(self):
        self.tmp.cleanup()
    def post(self,c,path,payload,method='POST'):
        csrf=c.get('/api/session').json['csrf']
        return c.open(path,method=method,json=payload,headers={'X-CSRF-Token':csrf})
    def login(self,c,login,password):
        r=self.post(c,'/api/login',dict(login=login,password=password))
        self.assertEqual(r.status_code,200,r.json)
        return r.json
    def member(self):
        r=self.post(self.owner,'/api/users',dict(login='worker',name='Логист',password='worker-initial-test-password',role='member'))
        self.assertEqual(r.status_code,201,r.json)
        c=self.app.test_client()
        self.login(c,'worker','worker-initial-test-password')
        return c
    def test_auth_and_csrf(self):
        anon=self.app.test_client()
        self.assertEqual(anon.get('/api/state').status_code,401)
        self.assertEqual(anon.post('/api/login',json={}).status_code,403)
        self.assertNotIn('password',self.owner.get('/api/session').json['user'])
        self.assertEqual(self.owner.get('/server.py').status_code,404)
        self.assertEqual(self.owner.get('/.env').status_code,404)
        r=self.post(self.owner,'/api/login',dict(login='admin',password='wrong'))
        self.assertEqual(r.status_code,401)
    def test_forced_change_permissions_and_disable(self):
        c=self.member()
        self.assertEqual(c.get('/api/state').status_code,403)
        self.assertEqual(self.post(c,'/api/password',dict(old='worker-initial-test-password',password='worker-changed-test-password')).status_code,200)
        self.assertEqual(c.get('/api/state').status_code,401)
        self.login(c,'worker','worker-changed-test-password')
        self.assertEqual(c.get('/api/state').status_code,200)
        self.assertEqual(c.get('/api/users').status_code,403)
        self.assertEqual(self.post(c,'/api/users',dict(login='evil',name='Evil',password='some-strong-test-password',role='admin')).status_code,403)
        users=self.owner.get('/api/users').json['users']
        owner=next(u for u in users if u['role']=='owner')
        worker=next(u for u in users if u['role']=='member')
        self.assertEqual(self.post(self.owner,'/api/users/'+owner['id'],dict(active=False),method='PATCH').status_code,403)
        self.assertEqual(self.post(self.owner,'/api/users/'+worker['id'],dict(active=False),method='PATCH').status_code,200)
        self.assertEqual(c.get('/api/state').status_code,401)
        self.assertEqual(self.post(c,'/api/login',dict(login='worker',password='worker-changed-test-password')).status_code,401)
    def test_shared_data_conflicts_and_restart(self):
        state=self.owner.get('/api/state').json
        state['data']['notes'].append(dict(id='note-1',text='Контакт с клиентом',date='2026-11-15T06:00:00Z',partner=''))
        r=self.post(self.owner,'/api/state',state,method='PUT')
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.post(self.owner,'/api/state',state,method='PUT').status_code,409)
        other=self.app.test_client()
        self.login(other,'admin','owner-changed-test-password')
        self.assertEqual(other.get('/api/state').json['data']['notes'][0]['text'],'Контакт с клиентом')
        restarted=create_app(self.config).test_client()
        self.login(restarted,'admin','owner-changed-test-password')
        self.assertEqual(restarted.get('/api/state').json['revision'],1)
    def test_import_validation(self):
        state=self.owner.get('/api/state').json
        state['data']['notes']=[dict(id="');alert(1)//",text='bad',date='',partner='')]
        self.assertEqual(self.post(self.owner,'/api/state',state,method='PUT').status_code,400)
        state['data']['notes']=[dict(id='same',text='1',date='',partner=''),dict(id='same',text='2',date='',partner='')]
        self.assertEqual(self.post(self.owner,'/api/state',state,method='PUT').status_code,400)
        self.assertEqual(self.owner.get('/api/state').json['revision'],0)
    def test_reset_revokes_sessions(self):
        c=self.member()
        uid=next(u['id'] for u in self.owner.get('/api/users').json['users'] if u['login']=='worker')
        self.assertEqual(self.post(self.owner,'/api/users/'+uid,dict(password='new-temporary-password'),method='PATCH').status_code,200)
        self.assertEqual(c.get('/api/session').json['user'],None)
        self.assertEqual(self.post(c,'/api/login',dict(login='worker',password='worker-initial-test-password')).status_code,401)
        self.login(c,'worker','new-temporary-password')
        self.assertEqual(c.get('/api/state').status_code,403)
    def test_lockout(self):
        c=self.app.test_client()
        for _ in range(10):
            self.assertEqual(self.post(c,'/api/login',dict(login='nobody',password='wrong')).status_code,401)
        self.assertEqual(self.post(c,'/api/login',dict(login='nobody',password='wrong')).status_code,429)
    def test_admin_cannot_promote_or_disable_owner(self):
        self.assertEqual(self.post(self.owner,'/api/users',dict(login='manager',name='Администратор',password='manager-initial-password',role='admin')).status_code,201)
        c=self.app.test_client()
        self.login(c,'manager','manager-initial-password')
        self.post(c,'/api/password',dict(old='manager-initial-password',password='manager-changed-password'))
        self.login(c,'manager','manager-changed-password')
        owner=next(u for u in c.get('/api/users').json['users'] if u['role']=='owner')
        self.assertEqual(self.post(c,'/api/users/'+owner['id'],dict(active=False),method='PATCH').status_code,403)
        self.assertEqual(self.post(c,'/api/users',dict(login='newadmin',name='Another',password='newadmin-test-password',role='admin')).status_code,403)
    def test_admin_edits_and_deletes_member(self):
        worker = self.member()
        self.post(self.owner, '/api/users', dict(login='manager', name='Manager', password='manager-initial-password', role='admin'))
        manager = self.app.test_client()
        self.login(manager, 'manager', 'manager-initial-password')
        self.post(manager, '/api/password', dict(old='manager-initial-password', password='manager-changed-password'))
        self.login(manager, 'manager', 'manager-changed-password')
        users = manager.get('/api/users').json['users']
        uid = next(u['id'] for u in users if u['login'] == 'worker')
        path = '/api/users/' + uid
        self.assertEqual(self.post(manager, path, dict(login='admin'), method='PATCH').status_code, 409)
        self.assertEqual(self.post(manager, path, dict(name=''), method='PATCH').status_code, 400)
        self.assertEqual(self.post(manager, path, dict(name='Новое имя', login='newworker'), method='PATCH').status_code, 200)
        self.assertIsNone(worker.get('/api/session').json['user'])
        edited = next(u for u in manager.get('/api/users').json['users'] if u['id'] == uid)
        self.assertEqual((edited['name'], edited['login']), ('Новое имя', 'newworker'))
        self.login(worker, 'newworker', 'worker-initial-test-password')
        with self.app.crm_db() as q:
            q('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)',
              ('doc-user', 'test.txt', 1, 'YQ==', 'pending', 0, uid, 'Логист', 1, ''))
        state = self.owner.get('/api/state').json
        for u in users:
            if u['role'] != 'member':
                self.assertEqual(self.post(manager, '/api/users/' + u['id'], {}, method='DELETE').status_code, 403)
        self.assertEqual(self.post(manager, path, {}, method='DELETE').status_code, 200)
        self.assertIsNone(worker.get('/api/session').json['user'])
        self.assertNotIn(uid, [u['id'] for u in manager.get('/api/users').json['users']])
        self.assertEqual(self.post(manager, path, {}, method='DELETE').status_code, 404)
        self.assertEqual(self.post(manager, path, dict(active=True), method='PATCH').status_code, 404)
        self.assertEqual(self.post(worker, '/api/login', dict(login='newworker', password='worker-initial-test-password')).status_code, 401)
        self.assertEqual(self.owner.get('/api/state').json, state)
        with self.app.crm_db() as q:
            self.assertEqual(q('SELECT uploader_id FROM documents WHERE id=?', ('doc-user',)).fetchone()['uploader_id'], uid)
        self.assertEqual(self.post(manager, '/api/users', dict(name='Replacement', login='newworker', password='replacement-password')).status_code, 201)

    def test_health_and_hashed_passwords(self):
        self.assertEqual(self.owner.get('/healthz').status_code,200)
        with self.app.crm_db() as q:
            row=q('SELECT password FROM users WHERE login=?',('admin',)).fetchone()
            self.assertNotIn('owner-changed-test-password',row['password'])
            self.assertTrue(row['password'].startswith('scrypt:'))
        self.assertGreaterEqual(len(self.owner.get('/api/audit').json['events']),3)

    def ready_member(self):
        member = self.member()
        self.post(member, '/api/password', dict(old='worker-initial-test-password', password='worker-changed-test-password'))
        user = self.login(member, 'worker', 'worker-changed-test-password')['user']
        return member, user['id']

    def upload(self, client, path, data):
        csrf = client.get('/api/session').json['csrf']
        return client.post(path, data=data, headers={'X-CSRF-Token': csrf})

    def write(self, client, state):
        return self.post(client, '/api/state', state, 'PUT')

    def test_item_deletion_permissions_and_revision(self):
        member, _ = self.ready_member()
        state = self.owner.get('/api/state').json
        state['data']['items'] = [
            {'id': kind, 'name': 'Тестовая заявка', 'kind': kind, 'from': 'Москва', 'to': 'Тула'}
            for kind in ('cargo', 'truck')
        ]
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        for kind in ('cargo', 'truck'):
            path = f'/api/items/{kind}/deletion'
            state = member.get('/api/state').json
            revision = state['revision']
            mark = dict(action='mark', revision=revision, reason='Устарела')
            self.assertEqual(self.post(member, path, dict(action='delete', revision=revision)).status_code, 403)
            state['data']['items'] = [item for item in state['data']['items'] if item['id'] != kind]
            self.assertEqual(self.write(member, state).status_code, 403)
            self.assertEqual(self.post(member, path, mark).status_code, 200)
            self.assertEqual(self.post(member, path, mark).status_code, 409)
            latest = self.owner.get('/api/state').json
            item = next(item for item in latest['data']['items'] if item['id'] == kind)
            self.assertTrue(item['deletionRequested'])
            self.assertEqual(item['deletionReason'], 'Устарела')
            current = latest['revision']
            item.pop('deletionRequested')
            self.assertEqual(self.write(member, latest).status_code, 403)
            self.assertEqual(self.post(member, path, dict(action='cancel', revision=current)).status_code, 403)
            self.assertEqual(self.post(self.owner, path, dict(action='cancel', revision=current)).status_code, 200)
            self.assertEqual(self.post(self.owner, path, dict(action='delete', revision=current + 1)).status_code, 200)
        self.assertEqual(self.owner.get('/api/state').json['data']['items'], [])

    def test_partner_archive_contacts_existing_cards_and_excel(self):
        import io
        from openpyxl import Workbook

        state = self.owner.get('/api/state').json
        contacts = json.dumps([dict(phone='+70000000002', name='Анна', position='Логист', comment='После 10')])
        partner = dict(id='partner', name='Тестовая компания', inn='0000000000', role='Клиент',
                       phone='+70000000001', extraPhones=contacts)
        raw = state['data']
        raw['partners'].append(partner)
        with self.app.crm_db() as q:
            q('UPDATE workspace SET data=? WHERE id=1', (json.dumps(raw),))
        state = self.owner.get('/api/state').json
        self.assertEqual(state['data']['partners'][0]['role'], 'Грузоотправитель')
        target = state['data']['partners'][0]
        target['role'] = 'Оба'
        target.update(archived=True, archiveReason='  ')
        self.assertEqual(self.write(self.owner, state).status_code, 400)
        target['archived'] = False
        r = self.write(self.owner, state)
        self.assertEqual(r.status_code, 200, r.json)
        result = self.post(self.owner, '/api/partners/partner/work', dict(action='take', revision=r.json['revision']))
        self.assertEqual(result.status_code, 200, result.json)
        state = self.owner.get('/api/state').json
        state['data']['partners'][0].update(archived=True, archiveReason='Договор завершён')
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        state = self.owner.get('/api/state').json
        archived = state['data']['partners'][0]
        self.assertEqual((archived['role'], archived['phone'], archived['extraPhones']), ('Оба', partner['phone'], contacts))
        self.assertNotIn('workById', archived)
        self.assertIn('Договор завершён', state['data']['notes'][0]['text'])
        take = self.post(self.owner, '/api/partners/partner/work', dict(action='take', revision=state['revision']))
        self.assertEqual(take.status_code, 409)
        state['data']['partners'].append(dict(id='another', name='Другое название', inn='00000 00000'))
        self.assertEqual(self.write(self.owner, state).status_code, 409)
        book = Workbook()
        book.active.append(['Компания', 'ИНН'])
        book.active.append(['Импортированный дубль', '00000 00000'])
        book.active.append(['Тестовая компания', ''])
        stream = io.BytesIO()
        book.save(stream)
        stream.seek(0)
        result = self.upload(self.owner, '/api/import/excel', dict(file=(stream, 'contacts.xlsx'), mode='commit', revision=str(state['revision'])))
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual((result.json['added'], result.json['skipped']), (0, 2))
        state['data']['partners'].pop()
        state['data']['partners'][0].update(archived=False, archiveReason='')
        restored = self.write(self.owner, state)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(len(restored.json['data']['notes']), 2)

    def test_concurrent_workspace_writes_do_not_overwrite(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        clients = [self.owner, self.app.test_client()]
        self.login(clients[1], 'admin', 'owner-changed-test-password')
        barrier = Barrier(2)

        def save(index):
            client = clients[index]
            state = client.get('/api/state').json
            state['data']['notes'].append(dict(id=f'concurrent-{index}', text='Синтетическая запись'))
            barrier.wait(timeout=5)
            return self.write(client, state).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, [0, 1])), [200, 409])
        state = self.owner.get('/api/state').json
        self.assertEqual(state['revision'], 1)
        self.assertEqual(len(state['data']['notes']), 1)

    def test_archived_partner_preserves_tasks_and_blocks_queue(self):
        _, member_id = self.ready_member()
        mobile = self.app.test_client()
        self.login(mobile, 'mobile:worker', 'worker-changed-test-password')
        state = self.owner.get('/api/state').json
        state['data']['partners'].append(dict(id='partner', name='Архивируемая компания'))
        state['data']['calls'].append(dict(id='cold', name='Холодный звонок', taskType='Холодный звонок',
            assigneeId=member_id, date='2026-12-05', time='10:00', partner='partner'))
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        self.assertEqual(len(mobile.get('/api/state').json['data']['calls']), 1)
        state = self.owner.get('/api/state').json
        state['data']['partners'][0].update(archived=True, archiveReason='Контакт завершён')
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        state = self.owner.get('/api/state').json
        self.assertEqual(len(state['data']['calls']), 1)
        self.assertEqual(mobile.get('/api/state').json['data']['calls'], [])
        self.assertEqual(self.write(mobile, dict(revision=state['revision'], data=dict(taskId='cold', done=True))).status_code, 403)
        state['data']['calls'].append(dict(id='fresh', name='Новый звонок', partner='partner'))
        self.assertEqual(self.write(self.owner, state).status_code, 409)

    def test_calendar_web_mobile_permissions_and_repeat(self):
        member, member_id = self.ready_member()
        mobile = self.app.test_client()
        self.login(mobile, 'mobile:worker', 'worker-changed-test-password')
        state = self.owner.get('/api/state').json
        state['data']['calls'] = [
            dict(id='private', name='Только для администратора', date='2026-12-01', time='10:00'),
            dict(id='scheduled', name='Встреча', taskType='Встреча', date='2026-12-02', time='09:00',
                 assigneeId=member_id, repeat='weekly', reminder='60', done=False),
        ]
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        state = member.get('/api/state').json
        self.assertEqual([c['id'] for c in state['data']['calls']], ['scheduled'])
        self.assertEqual(mobile.get('/api/state').json['data']['calls'], state['data']['calls'])
        self.assertEqual(self.post(member, '/api/calls/private/deletion', dict(action='mark', revision=state['revision'])).status_code, 404)
        state['data']['calls'][0]['assigneeId'] = ''
        self.assertEqual(self.write(member, state).status_code, 403)
        done = self.write(mobile, dict(revision=state['revision'], data=dict(taskId='scheduled', done=True)))
        self.assertEqual(done.status_code, 200, done.json)
        self.assertEqual(self.write(mobile, dict(revision=state['revision'], data=dict(taskId='scheduled', done=True))).status_code, 409)
        data = member.get('/api/state').json
        first, repeat = data['data']['calls']
        self.assertTrue(first['done'])
        self.assertEqual((repeat['date'], repeat['reminder'], repeat['done']), ('2026-12-09', '60', False))
        first['done'] = False
        self.assertEqual(self.write(member, data).status_code, 200)
        data = member.get('/api/state').json
        data['data']['calls'][0]['done'] = True
        self.assertEqual(len(self.write(member, data).json['data']['calls']), 2)
        self.assertEqual(len(self.owner.get('/api/state').json['data']['calls']), 3)

    def test_chat_files_read_receipts_private_and_mobile(self):
        import io

        member, member_id = self.ready_member()
        owner_id = self.owner.get('/api/session').json['user']['id']
        third = self.app.test_client()
        self.post(self.owner, '/api/users', dict(login='third', name='Третий', password='third-initial-test-password'))
        self.login(third, 'third', 'third-initial-test-password')
        self.post(third, '/api/password', dict(old='third-initial-test-password', password='third-changed-test-password'))
        self.login(third, 'third', 'third-changed-test-password')
        state = self.owner.get('/api/state').json
        state['data']['partners'].append(dict(id='company', name='Компания'))
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        result = self.upload(self.owner, '/api/chat', dict(peer=member_id, text='Личное',
            kind='partners', card_id='company', file=(io.BytesIO(b'test file'), 'test.txt')))
        self.assertEqual(result.status_code, 201, result.json)
        mid = result.json['id']
        path = '/api/chat/files/' + mid
        self.assertEqual(third.get(path).status_code, 404)
        self.assertEqual(third.get('/api/chat?peer=' + owner_id).json['messages'], [])
        self.assertEqual(third.get('/api/chat/unread').json['conversations'], [])
        self.assertEqual(member.get(path).data, b'test file')
        self.assertEqual(member.get('/api/chat?peer=' + owner_id).json['messages'][0]['card_id'], 'company')
        self.assertEqual(member.get('/api/chat/unread').json['conversations'][0]['count'], 1)
        self.assertEqual(self.post(member, '/api/chat/read', dict(ids=[mid])).status_code, 200)
        self.assertEqual(self.post(member, '/api/chat/read', dict(ids=[mid])).status_code, 200)
        self.assertEqual(member.get('/api/chat/unread').json['conversations'], [])
        self.assertEqual(self.owner.get('/api/chat?peer=' + member_id).json['messages'][0]['read_count'], 1)
        mobile = self.app.test_client()
        self.login(mobile, 'mobile:worker', 'worker-changed-test-password')
        self.assertNotIn('card_id', mobile.get('/api/chat?peer=' + owner_id).json['messages'][0])
        self.assertEqual(self.post(mobile, '/api/chat', dict(text='Общий')).status_code, 201)
        self.assertEqual(third.get('/api/chat').json['messages'][0]['body'], 'Общий')

    def test_chat_history_cursor_stays_within_conversation(self):
        member, member_id = self.ready_member()
        owner_id = self.owner.get('/api/session').json['user']['id']
        with self.app.crm_db() as q:
            for index in range(201):
                q('INSERT INTO chat_messages VALUES (?,?,?,?,?,?)',
                  (f'group-{index:03}', owner_id, 'Владелец', '', str(index), 1000))
            q('INSERT INTO chat_messages VALUES (?,?,?,?,?,?)',
              ('private', owner_id, 'Владелец', member_id, 'Только получателю', 1000))
        first = member.get('/api/chat').json
        self.assertEqual(len(first['messages']), 200)
        remaining = member.get('/api/chat', query_string={'before': first['before']}).json
        self.assertEqual([m['id'] for m in remaining['messages']], ['group-000'])
        self.assertEqual(remaining['before'], '')
        self.assertEqual(member.get('/api/chat?peer=' + owner_id).json['messages'][0]['id'], 'private')

    def make_contract(self):
        import io
        from docx import Document

        self.app.config['CONTRACT_COMPANY'] = dict(name='Синтетическая КСГ', inn='0000000000')
        state = self.owner.get('/api/state').json
        state['data']['partners'].append(dict(id='customer', name='Тестовый заказчик', inn='1111111111', kpp='111111111'))
        state['data']['deals'].append(dict(id='deal', name='Маршрут', status='Новая', partnerId='customer'))
        self.assertEqual(self.write(self.owner, state).status_code, 200)
        template = Document()
        paragraph = template.add_paragraph('Договор {{num')
        paragraph.add_run('ber}}').bold = True
        template.add_paragraph('{{company.name}} / {{partner.name}} / {{partner.kpp}} / {{route}}')
        stream = io.BytesIO()
        template.save(stream)
        stream.seek(0)
        result = self.upload(self.owner, '/api/contracts/templates', dict(name='Шаблон', file=(stream, 'contract.docx')))
        self.assertEqual(result.status_code, 201, result.json)
        data = dict(template=result.json['id'], partner='customer', deal='deal', route='Москва – Тула', rate='10000')
        contract = self.post(self.owner, '/api/contracts', data)
        self.assertEqual(contract.status_code, 201, contract.json)
        return contract.json, data

    def test_contract_generation_links_status_and_pdf(self):
        import io
        import shutil
        from docx import Document

        contract, data = self.make_contract()
        next_contract = self.post(self.owner, '/api/contracts', data).json
        self.assertNotEqual(contract['number'], next_contract['number'])
        cid = contract['id']
        path = '/api/contracts/' + cid
        response = self.owner.get(path + '/docx')
        self.assertEqual(response.status_code, 200)
        doc = Document(io.BytesIO(response.data))
        self.assertEqual(doc.paragraphs[0].text, 'Договор ' + contract['number'])
        self.assertIn('Тестовый заказчик / 111111111 / Москва – Тула', doc.paragraphs[1].text)
        self.assertEqual(len(self.owner.get('/api/contracts?partner=customer&deal=deal').json['contracts']), 2)
        self.assertEqual(self.owner.get('/api/contracts?deal=other').json['contracts'], [])
        status = dict(status='На согласовании', expected='Черновик')
        self.assertEqual(self.post(self.owner, path, status, 'PATCH').status_code, 200)
        self.assertEqual(self.post(self.owner, path, status, 'PATCH').status_code, 409)
        response = self.owner.get(path + '/pdf')
        self.assertEqual(response.status_code, 200 if shutil.which('libreoffice') else 503)
        if response.status_code == 200:
            self.assertTrue(response.data.startswith(b'%PDF-'))
            self.assertEqual(self.owner.get(path + '/pdf').data, response.data)

    def test_diadoc_adapter_signer_recipient_and_idempotency(self):
        from unittest.mock import Mock

        contract, _ = self.make_contract()
        cid = contract['id']
        self.assertEqual(self.owner.get('/api/diadoc/organizations?inn=1111111111').status_code, 503)
        member, _ = self.ready_member()
        self.assertEqual(member.get('/api/diadoc').status_code, 403)
        self.app.config['DIADOC'] = dict(token='fake-test-token', box='sender', signer='not-admin')
        path = '/api/diadoc/contracts/' + cid
        payload = dict(box='recipient', signature='dGVzdA==')
        self.assertEqual(self.post(self.owner, path, payload).status_code, 403)
        self.app.config['DIADOC']['signer'] = 'admin'
        with self.app.crm_db() as q:
            q('INSERT INTO contract_pdfs VALUES (?,?)', (cid, b'%PDF-mocked'))
        mock = Mock(side_effect=[
            ({'Organizations': [{'Boxes': [{'BoxId': 'wrong'}]}]}, 200),
            ({'Organizations': [{'Boxes': [{'BoxId': 'recipient'}]}]}, 200),
            ({'ToBoxId': 'recipient', 'MessageId': 'message', 'Entities': [{'EntityType': 'Attachment', 'EntityId': 'document'}]}, 200),
            ({'TaskId': 'task'}, 200), ({'Status': 'Success'}, 200),
            ({'Documents': []}, 200), ({'DocumentStatus': 'Signed'}, 200), (b'test', 200),
        ])
        self.app.config['DIADOC_TRANSPORT'] = mock
        self.assertEqual(self.post(self.owner, path, payload).status_code, 400)
        sent = self.post(self.owner, path, payload)
        self.assertEqual(sent.status_code, 201, sent.json)
        self.assertEqual(sent.json['entity_id'], 'document')
        self.assertEqual(mock.call_args.args[1], {'operationId': cid})
        self.assertEqual(mock.call_args.args[3]['DocumentAttachments'][0]['SignedContent']['Signature'], 'dGVzdA==')
        self.assertEqual(self.post(self.owner, path, payload).status_code, 200)
        self.assertEqual(mock.call_count, 3)
        self.assertEqual(self.post(self.owner, '/api/diadoc/invitations', dict(box='recipient')).json['TaskId'], 'task')
        self.assertEqual(self.owner.get('/api/diadoc/invitations/task').json['Status'], 'Success')
        self.assertEqual(self.owner.get('/api/diadoc/documents').json['Documents'], [])
        self.assertEqual(self.owner.get('/api/diadoc/documents/message/document').json['DocumentStatus'], 'Signed')
        self.assertEqual(self.owner.get('/api/diadoc/documents/message/document/file').data, b'test')

if __name__=='__main__':
    unittest.main(verbosity=2)
