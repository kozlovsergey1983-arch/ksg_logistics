"""Compatibility and transition rules for revisioned workspace records."""
import json
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import abort

MOSCOW = ZoneInfo('Europe/Moscow')
REPEAT = {'daily': 1, 'weekly': 7}


def normalize_partners(data):
    for partner in data['partners']:
        if partner.get('role') in (None, '', 'Клиент'):
            partner['role'] = 'Грузоотправитель'
    return data


def partner_keys(partner):
    inn = re.sub(r'\s+', '', partner.get('inn', '') or '')
    name = ' '.join((partner.get('name') or '').casefold().split())
    return inn, name


def same_partner(a, b):
    inn, name = partner_keys(a)
    other_inn, other_name = partner_keys(b)
    return inn == other_inn if inn and other_inn else bool(name and name == other_name)


def validate_partner(partner):
    reason = partner.get('archiveReason', '')
    if not isinstance(reason, str) or len(reason) > 1000 or (partner.get('archived') and not reason.strip()):
        abort(400, 'Укажи причину архивации длиной до 1000 символов')
    raw = partner.get('extraPhones', '')
    if raw == '':
        return
    try:
        contacts = json.loads(raw)
    except (ValueError, TypeError):
        abort(400, 'Некорректные дополнительные телефоны')
    if not isinstance(contacts, list) or len(contacts) > 30:
        abort(400, 'Допускается до 30 дополнительных телефонов')
    for contact in contacts:
        if (not isinstance(contact, dict) or set(contact) != {'phone', 'name', 'position', 'comment'}
                or any(not isinstance(v, str) or len(v) > 500 for v in contact.values())
                or not contact['phone'].strip()):
            abort(400, 'Некорректный дополнительный контакт')


def partner_transitions(data, previous, actor):
    old = {p['id']: p for p in previous['partners']}
    by_identity = {}
    for partner in data['partners']:
        prior = old.get(partner['id'])
        inn, name = partner_keys(partner)
        candidates = by_identity.setdefault(name, []) if name else []
        matched = [other for other in candidates if same_partner(other, partner)]
        if name:
            candidates.append(partner)
        if inn:
            by_inn = by_identity.setdefault(('inn', inn), [])
            matched.extend(by_inn)
            by_inn.append(partner)
        for other in matched:
            previous_other = old.get(other['id'])
            if not prior or not previous_other or not same_partner(prior, previous_other):
                abort(409, 'Такая карточка уже есть. Проверь также архив')
        was_archived = bool(prior and prior.get('archived'))
        archived = bool(partner.get('archived'))
        work = ('workById', 'workBy', 'workAt')
        if was_archived != archived:
            if not prior:
                abort(400, 'Сначала сохрани карточку')
            for key in work:
                partner.pop(key, None)
            action = 'Архивация: ' + partner['archiveReason'].strip() if archived else 'Восстановление из архива'
            data['notes'].insert(0, dict(id=str(uuid.uuid4()), partner=partner['id'],
                date=datetime.now(MOSCOW).isoformat(), text=f'{action} · {actor}'))
        elif any(partner.get(k) != (prior or {}).get(k) for k in work):
            abort(403, 'Назначение меняется отдельной кнопкой')


def task_transitions(data, previous, user, active_users):
    old = {c['id']: c for c in previous['calls']}
    member = user['role'] == 'member'
    own = user['id']
    incoming = {c['id']: c for c in data['calls']}
    if member:
        for cid, task in old.items():
            if task.get('assigneeId') == own:
                if cid not in incoming:
                    abort(403, 'Для удаления задачи отправь запрос')
            elif cid in incoming:
                abort(403, 'Доступны только свои задачи')
    archived = {p['id'] for p in data['partners'] if p.get('archived')}
    additions = []
    repeat_parents = {task.get('repeatParent') for task in (*old.values(), *data['calls'])}
    for task in data['calls']:
        prior = old.get(task['id'], {})
        if member and not prior:
            if task.get('assigneeId') not in (None, '', own):
                abort(403, 'Назначать задачи другим сотрудникам может администратор')
            task['assigneeId'] = own
        if member and prior and task.get('assigneeId') != own:
            abort(403, 'Переназначать задачи может администратор')
        assignee = task.get('assigneeId')
        if assignee is not None and not isinstance(assignee, str):
            abort(400, 'Неверный исполнитель')
        if assignee and assignee != prior.get('assigneeId') and assignee not in active_users:
            abort(400, 'Выбери активного сотрудника')
        if task.get('partner') in archived and (task['partner'] != prior.get('partner') or assignee != prior.get('assigneeId')):
            abort(409, 'Контрагент находится в архиве')
        for key in ('deletionRequested', 'deletionBy', 'deletionReason', 'deletionAt', 'repeatParent'):
            if task.get(key) != prior.get(key):
                abort(403, 'Поле изменяется сервером')
        validate_schedule(task, prior)
        next_task = repeat_task(task, prior, repeat_parents)
        if next_task and task.get('partner') not in archived and not task.get('deletionRequested'):
            additions.append(next_task)
    data['calls'].extend(additions)
    if member:
        data['calls'].extend(c for c in old.values() if c.get('assigneeId') != own)


def validate_schedule(task, prior):
    repeat = task.get('repeat', '')
    if repeat not in ('', *REPEAT):
        abort(400, 'Неверное расписание повтора')
    reminder = task.get('reminder', '')
    if reminder not in ('', '0', '15', '60', '1440'):
        abort(400, 'Неверный интервал напоминания')
    for field, pattern in [('date', r'\d{4}-\d{2}-\d{2}'), ('time', r'(?:[01]\d|2[0-3]):[0-5]\d')]:
        value = task.get(field, '')
        if value != prior.get(field, '') and (not value or not re.fullmatch(pattern, value)):
            abort(400, 'Неверная дата или время задачи')
    if task.get('date') and (repeat or task['date'] != prior.get('date')):
        try:
            scheduled = date.fromisoformat(task['date'])
            if repeat:
                scheduled + timedelta(days=REPEAT[repeat])
        except (ValueError, OverflowError):
            abort(400, 'Неверная дата задачи')
    if repeat and not task.get('date'):
        abort(400, 'Для повтора укажи дату')


def in_call_queue(task, archived):
    return (task.get('done') or task.get('taskType', 'Звонок') not in ('Звонок', 'Холодный звонок')
            or task.get('partner') not in archived)


def repeat_task(task, prior, repeat_parents):
    repeat = task.get('repeat')
    if repeat not in REPEAT or not task.get('done') or prior.get('done'):
        return None
    if task['id'] in repeat_parents:
        return None
    validate_schedule(task, prior)
    next_date = date.fromisoformat(task['date']) + timedelta(days=REPEAT[repeat])
    return dict(task, id=str(uuid.uuid4()), date=next_date.isoformat(), done=False, repeatParent=task['id'])
