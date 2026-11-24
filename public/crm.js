async function multipart(path, data) {
    const response = await fetch('/api' + path, {
        method: 'POST', headers: {'X-CSRF-Token': csrf}, body: data
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Ошибка загрузки');
    return result;
}

const repeatOptions = [['', 'Без повтора'], ['daily', 'Каждый день'], ['weekly', 'Каждую неделю']];
const reminderOptions = [['', 'Без напоминания'], ['0', 'В момент задачи'],
    ['15', 'За 15 минут'], ['60', 'За час'], ['1440', 'За сутки']];

function options(rows, selected) {
    return rows.map(([value, label]) =>
        `<option value="${esc(value)}" ${value === selected ? 'selected' : ''}>${esc(label)}</option>`
    ).join('');
}

async function callForm(id) {
    const task = db.calls.find(c => c.id === id) || {date: page === 'Календарь' && calendarDate || today()};
    const actor = currentUser;
    let assignment = '';
    if (actor.role !== 'member') {
        try {
            const result = await api('/task-assignees');
            if (actor !== currentUser) return;
            const users = result.users.map(u => [u.id, u.name]);
            if (task.assigneeId && !users.some(([uid]) => uid === task.assigneeId)) {
                users.push([task.assigneeId, 'Неактивный сотрудник']);
            }
            assignment = `<label>Исполнитель<select name="assigneeId">${options([['', 'Не назначен'], ...users], task.assigneeId)}</select></label>`;
        } catch (error) {
            return toast(error.message);
        }
    }
    const partners = db.partners.filter(p => (!p.archived && !p.deletionRequested) || p.id === task.partner);
    form(id ? 'Изменить задачу' : 'Новая задача',
        select('taskType', 'Тип', taskTypes, id ? taskType(task) : 'Прочее') +
        field('name', 'Что нужно сделать?', task.name) + field('phone', 'Телефон', task.phone, 'tel') +
        field('date', 'Дата', task.date, 'date') + field('time', 'Время (Москва)', task.time || '10:00', 'time') +
        field('topic', 'Тема / следующий шаг', task.topic) +
        `<label>Контрагент<select name="partner">${options([['', 'Без привязки'], ...partners.map(p => [p.id, p.name])], task.partner)}</select></label>
        ${assignment}<label>Повтор<select name="repeat">${options(repeatOptions, task.repeat)}</select></label>
        <label>Напоминание<select name="reminder">${options(reminderOptions, task.reminder)}</select></label>`,
        `saveCall(this,'${id || ''}')`);
}

function saveCall(form, id) {
    const value = values(form);
    if (!value.name.trim() || !value.date || !value.time) return toast('Укажи название, дату и время');
    const previous = db.calls.find(c => c.id === id);
    put('calls', {...previous, ...value, id: id || uid(), done: previous?.done || false,
        assigneeId: currentUser.role === 'member' ? currentUser.id : value.assigneeId});
}

let calendarDate = '', calendarMode = 'month';
function calendarMove(direction) {
    const date = new Date((calendarDate || today()) + 'T12:00:00Z');
    if (calendarMode === 'month') {
        date.setUTCDate(1);
        date.setUTCMonth(date.getUTCMonth() + direction);
    } else {
        date.setUTCDate(date.getUTCDate() + direction * (calendarMode === 'week' ? 7 : 1));
    }
    calendarDate = date.toISOString().slice(0, 10);
    render();
}

function callInQueue(task) {
    return task.done || !['Звонок', 'Холодный звонок'].includes(taskType(task)) ||
        !db.partners.some(p => p.id === task.partner && p.archived);
}

function taskInstant(task) {
    return Date.parse(`${task.date}T${task.time || '00:00'}:00+03:00`);
}

function calendarGrid(start, end, tasks, base) {
    if (calendarMode === 'day') return '';
    const counter = new Map();
    for (const task of tasks) {
        if (!task.done) counter.set(task.date, (counter.get(task.date) || 0) + 1);
    }
    const week = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const cells = week.map(day => `<span class="calendar-weekday">${day}</span>`);
    for (let i = 0; i < (start.getUTCDay()+6)%7; i++) cells.push('<span></span>');
    for (const day = new Date(start); day <= end; day.setUTCDate(day.getUTCDate()+1)) {
        const key = day.toISOString().slice(0, 10);
        const count = counter.get(key) || 0;
        cells.push(`<button type="button" ${key === base ? 'class="active"' : ''} ${key === today() ? 'aria-current="date"' : ''}
            aria-label="${key}${count ? ', задач: ' + count : ''}" onclick="calendarDate='${key}';calendarMode='day';render()">
            ${day.getUTCDate()}${count ? ` <small>${count}</small>` : ''}</button>`);
    }
    return `<div class="calendar-grid">${cells.join('')}</div>`;
}

function calendarShell() {
    const base = calendarDate || today();
    const start = new Date(base + 'T12:00:00Z');
    const end = new Date(start);
    if (calendarMode === 'month') {
        start.setUTCDate(1);
        end.setUTCMonth(end.getUTCMonth() + 1, 0);
    } else if (calendarMode === 'week') {
        start.setUTCDate(start.getUTCDate() - (start.getUTCDay() + 6) % 7);
        end.setTime(start.getTime());
        end.setUTCDate(end.getUTCDate() + 6);
    }
    const key = date => date.toISOString().slice(0, 10);
    const tasks = db.calls.filter(c => c.date >= key(start) && c.date <= key(end))
        .sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
    return heading('Календарь', 'Звонки, встречи и задачи по московскому времени', addbtn('Новая задача', 'callForm()')) +
        `<div class="actions"><button onclick="calendarMove(-1)">← Назад</button>
        <button onclick="calendarDate='';render()">Сегодня</button><button onclick="calendarMove(1)">Вперёд →</button>
        <input aria-label="Дата" type="date" value="${base}" onchange="calendarDate=this.value;render()">
        <select aria-label="Период" onchange="calendarMode=this.value;render()">${options([['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']], calendarMode)}</select></div>
        <section class="panel"><h2>${key(start)} – ${key(end)}</h2>
        ${calendarGrid(start, end, tasks, base)}
        ${tasks.map(c => `<div class="row"><div><strong>${esc(c.date + ' ' + c.time)} · ${esc(c.name)}</strong>
        <p>${esc(c.topic)} ${c.partner && db.partners.some(p => p.id === c.partner) ? `<button onclick="partnerForm('${c.partner}')">${esc(db.partners.find(p => p.id === c.partner).name)}</button>` : ''}</p>
        ${c.done ? '<span class="badge">Выполнена</span>' : taskInstant(c) < Date.now() ? '<span class="badge amber">Просрочена</span>' : ''}
        </div><div><button onclick="callForm('${c.id}')">Перенести / изменить</button>
        <button onclick="finishCall('${c.id}')">${c.done ? 'Вернуть' : 'Готово'}</button></div></div>`).join('') || 'Нет задач на выбранный период'}
        </section>`;
}

const notifiedTasks = new Set();
function taskReminders() {
    for (const task of db.calls) {
        if (!callInQueue(task) || task.done || task.deletionRequested || task.assigneeId !== currentUser.id || !task.reminder) continue;
        const key = [currentUser.id, task.id, task.date, task.time, task.reminder].join(':');
        const instant = taskInstant(task);
        if (Date.now() >= instant - Number(task.reminder) * 60000 && Date.now() <= instant + 15 * 60000 && !notifiedTasks.has(key)) {
            notifiedTasks.add(key);
            toast('Напоминание · ' + task.name);
            if (window.Notification?.permission === 'granted') new Notification('Задача КСГ', {body: task.name});
        }
    }
}

let chatPeer = '', chatPolling = false, previousUnread = 0, syncActor = '';
let chatPages = new Map(), chatBefore = '', viewingHistory = false;
function chatShell() {
    return heading('Мессенджер', 'Общий чат команды и личные переписки',
        '<button onclick="enableNotifications()">Уведомления браузера</button>') +
        `<section class="panel"><label>Чат <select id="chatPeer" onchange="chatSelect(this.value)"><option value="">Общий чат</option></select></label>
        <button id="chatHistory" hidden onclick="loadChat(true)">Предыдущие сообщения</button>
        <div id="chatMessages" aria-live="polite"></div>
        <form id="chatForm" onsubmit="sendChat(this);return false">
        <label>Сообщение<textarea name="text" maxlength="10000"></textarea></label>
        <label>Фото или файл<input name="file" type="file"></label>
        <label>Карточка<select name="card">${options([['', 'Без карточки'],
            ...db.partners.map(p => ['partners:' + p.id, 'Контрагент · ' + p.name]),
            ...db.deals.map(d => ['deals:' + d.id, 'Сделка · ' + d.name])], '')}</select></label>
        <button class="primary">Отправить</button></form></section>`;
}

async function enableNotifications() {
    if (!window.Notification) return toast('Системные уведомления недоступны в этом браузере');
    if (await Notification.requestPermission() !== 'granted') toast('Уведомления остаются доступны в CRM');
}

function resetChatHistory() {
    chatPages.clear();
    chatBefore = '';
    viewingHistory = false;
}

function chatSelect(peer) {
    chatPeer = peer;
    resetChatHistory();
    $('#chatMessages').innerHTML = '';
    $('#chatMessages').renderedMessages = '';
    $('#chatHistory').hidden = true;
    loadChat();
}

async function loadChat(older = false) {
    if (!$('#chatMessages') || !currentUser || (older && (chatPolling || !chatBefore))) return;
    if (chatPolling) return;
    chatPolling = true;
    const target = $('#chatMessages'), peer = chatPeer, actor = currentUser.id;
    try {
        const query = new URLSearchParams({peer});
        if (older) query.set('before', chatBefore);
        const [people, result] = await Promise.all([api('/chat/users'), api('/chat?' + query)]);
        if ($('#chatMessages') !== target || chatPeer !== peer || currentUser?.id !== actor) return;
        const peers = people.users.filter(u => u.id !== actor).map(u => [u.id, u.name + (u.online ? ' · онлайн' : '')]);
        if (peer && !peers.some(([id]) => id === peer)) peers.push([peer, 'Неактивный сотрудник']);
        const peerSelect = $('#chatPeer');
        if (document.activeElement !== peerSelect) peerSelect.innerHTML = options([['', 'Общий чат'], ...peers], peer);
        if (older) viewingHistory = true;
        if (older || !viewingHistory) chatBefore = result.before;
        if (!viewingHistory) chatPages.clear();
        for (const message of result.messages) chatPages.set(message.id, message);
        $('#chatHistory').hidden = !chatBefore;
        const messages = [...chatPages.values()].sort((a, b) => a.created - b.created || (a.id < b.id ? -1 : 1));
        const content = messages.map(m => {
            const card = m.kind && db[m.kind]?.find(c => c.id === m.card_id);
            const openCard = m.kind === 'partners' ? 'partnerForm' : 'dealForm';
            return `<div class="row"><div><strong>${esc(m.sender_name)}</strong><p class="chat-text">${esc(m.body)}</p>
                ${m.filename && /\.(?:jpe?g|png|gif|webp)$/i.test(m.filename) ? `<img class="chat-photo" loading="lazy" src="/api/chat/files/${encodeURIComponent(m.id)}?preview=1" alt="${esc(m.filename)}">` : ''}
                ${m.filename ? `<a href="/api/chat/files/${encodeURIComponent(m.id)}">Скачать ${esc(m.filename)}</a>` : ''}
                ${card ? `<button onclick="${openCard}('${card.id}')">${esc(card.name)}</button>` : esc(m.title)}</div>
                <small>${new Date(m.created).toLocaleString('ru-RU', {timeZone: 'Europe/Moscow'})}
                ${m.sender === actor && m.read_count ? ' · прочитано (' + m.read_count + ')' : ''}</small></div>`;
        }).join('') || '<p>Сообщений пока нет</p>';
        if (target.renderedMessages !== content) {
            const oldHeight = target.scrollHeight;
            const nearBottom = oldHeight - target.clientHeight - target.scrollTop < 40;
            target.innerHTML = content;
            target.renderedMessages = content;
            if (older) target.scrollTop += target.scrollHeight - oldHeight;
            else if (nearBottom) target.scrollTop = target.scrollHeight;
        }
        const ids = result.messages.filter(m => m.sender !== actor && !m.read_by_me).map(m => m.id);
        if (!document.hidden && ids.length) await api('/chat/read', 'POST', {ids});
    } catch (error) {
        if ($('#chatMessages') === target) toast(error.message);
    } finally {
        chatPolling = false;
    }
}

async function sendChat(form) {
    const button = form.querySelector('button');
    button.disabled = true;
    const payload = new FormData(form);
    payload.set('peer', chatPeer);
    const [kind, id] = payload.get('card').split(':');
    if (id) {
        payload.set('kind', kind);
        payload.set('card_id', id);
    }
    if (!payload.get('file')?.size) payload.delete('file');
    try {
        await multipart('/chat', payload);
        form.reset();
        await loadChat();
    } catch (error) {
        toast(error.message);
    } finally {
        button.disabled = false;
    }
}

let backgroundBusy = false;
async function pollCrm() {
    if (!currentUser || currentUser.must_change || backgroundBusy) return;
    const actor = currentUser.id;
    if (syncActor !== actor) {
        syncActor = actor;
        previousUnread = 0;
        chatPeer = '';
        resetChatHistory();
    }
    backgroundBusy = true;
    try {
        const unread = await api('/chat/unread');
        if (currentUser?.id !== actor) return;
        const count = unread.conversations.reduce((sum, peer) => sum + peer.count, 0);
        const nav = $('#chatNav');
        if (nav) nav.textContent = 'Мессенджер' + (count ? ' (' + count + ')' : '');
        if (count > previousUnread && (page !== 'Мессенджер' || document.hidden)) {
            toast('Новые сообщения · ' + count);
            if (window.Notification?.permission === 'granted') new Notification('Сообщения КСГ', {body: 'Непрочитанных: ' + count});
        }
        previousUnread = count;
        if (page === 'Мессенджер') await loadChat();
        if (!busy && !$('#modal').open && ['Обзор', 'Календарь', 'Задачи и звонки'].includes(page)) {
            const baseline = revision;
            const next = await api('/state');
            if (currentUser?.id === actor && !busy && revision === baseline && next.revision > baseline && !$('#modal').open &&
                ['Обзор', 'Календарь', 'Задачи и звонки'].includes(page)) {
                db = next.data;
                revision = next.revision;
                $('#syncStatus').textContent = 'Общая база · версия ' + revision;
                render();
            }
        }
        if (currentUser?.id === actor) taskReminders();
    } catch {
        // A transient failure is retried on the next poll.
    } finally {
        backgroundBusy = false;
    }
}
setInterval(pollCrm, 8000);
