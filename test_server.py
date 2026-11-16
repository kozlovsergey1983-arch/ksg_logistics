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

if __name__=='__main__':
    unittest.main(verbosity=2)
