"""Owner password recovery: run inside the app container; needs DATABASE_URL and SECRET_KEY."""
import getpass
import sys
from server import create_app
from werkzeug.security import generate_password_hash

if __name__ == '__main__':
    if sys.argv[1:] != ['reset-owner']:
        raise SystemExit('Usage: python manage.py reset-owner')
    app=create_app()
    password=getpass.getpass('New owner password (12–256 characters): ')
    if not 12 <= len(password) <= 256 or password != getpass.getpass('Repeat password: '):
        raise SystemExit('Invalid password or passwords differ')
    with app.crm_db() as q:
        owner=q("SELECT id,login FROM users WHERE role='owner'").fetchone()
        q('UPDATE users SET password=?,must_change=1,active=1 WHERE id=?',(generate_password_hash(password),owner['id']))
        q('DELETE FROM sessions WHERE user_id=?',(owner['id'],))
        q('DELETE FROM attempts WHERE login=?',(owner['login'],))
    print('Owner temporary password reset; all sessions revoked. Sign in and change it.')
