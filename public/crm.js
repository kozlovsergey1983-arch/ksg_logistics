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

let chatPeer = null, previousUnread = 0, syncActor = '';
let chatPages = new Map(), chatBefore = '', viewingHistory = false;
let chatDialogs = [], chatRequest = 0, unreadBoundary = '', chatReadBusy = false;

function chatShell() {
    return `<section class="chat-workspace">
        <header class="chat-top"><button type="button" id="chatBack" hidden onclick="chatSelect(null)">‹ Назад</button>
        <div><h1 id="chatTitle">Мессенджер</h1><small id="chatPresence"></small></div>
        <button type="button" id="chatBell" aria-label="Уведомления браузера" onclick="enableNotifications()">🔔</button></header>
        <div id="chatList"><input type="search" id="chatSearch" placeholder="Найти сотрудника" aria-label="Найти сотрудника" oninput="renderDialogs()">
        <div id="chatDialogs" aria-live="polite">Загрузка диалогов…</div></div>
        <div id="chatThread" hidden>
          <div class="chat-log" id="chatLog" onscroll="markVisibleChat()">
            <button type="button" id="chatHistory" hidden onclick="loadChat(true)">Предыдущие сообщения</button>
            <div id="chatMessages" aria-live="polite"></div>
          </div>
          <form id="chatForm" onsubmit="sendChat(this);return false">
            <div class="chat-composer">
                <button type="button" aria-label="Добавить вложение" onclick="toggleChatAttachment()">＋</button>
                <textarea name="text" rows="1" maxlength="10000" placeholder="Сообщение" aria-label="Сообщение"></textarea>
                <button type="submit" class="primary">Отправить</button>
            </div>
            <div id="chatAttachment" hidden>
              <label>Фото или файл<input name="file" type="file"></label>
              <label>Карточка<select name="card">${options([['', 'Без карточки'],
                ...db.partners.map(p => ['partners:' + p.id, 'Контрагент · ' + p.name]),
                ...db.deals.map(d => ['deals:' + d.id, 'Сделка · ' + d.name])], '')}</select></label>
            </div>
          </form>
        </div></section>`;
}

async function enableNotifications() {
    if (!window.Notification) return toast('Системные уведомления недоступны в этом браузере');
    try {
        if (await Notification.requestPermission() !== 'granted') toast('Уведомления остаются доступны в CRM');
    } catch {
        toast('В Android включи уведомления через настройки приложения');
    }
}

function resetChatHistory() {
    chatRequest++;
    chatPages.clear();
    chatBefore = '';
    unreadBoundary = '';
    viewingHistory = false;
}

function resetChat() {
    chatPeer = null;
    chatDialogs = [];
    resetChatHistory();
}

function showChatNavCount(count) {
    const nav = $('#chatNav');
    const title = 'Мессенджер' + (count ? ' (' + count + ')' : '');
    if (nav && nav.textContent !== title) nav.textContent = title;
}

function renderDialogs() {
    const target = $('#chatDialogs');
    if (!target) return;
    const query = $('#chatSearch').value.trim().toLocaleLowerCase('ru');
    const visible = chatDialogs.filter(dialog => dialog.name.toLocaleLowerCase('ru').includes(query));
    const snapshot = JSON.stringify([query, visible]);
    if (target.renderedDialogs === snapshot) return;
    target.renderedDialogs = snapshot;
    const buttons = visible.map(dialog => {
        const initials = dialog.peer ? dialog.name.split(/\s+/).slice(0, 2).map(word => word[0]).join('') : 'КСГ';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chat-dialog' + (dialog.count ? ' chat-unread' : '');
        button.innerHTML = `<span class="chat-avatar" aria-hidden="true">${esc(initials)}</span>
            <span class="chat-dialog-name"><strong>${esc(dialog.name)}</strong>
            <small>${esc(dialog.preview || (dialog.peer ? 'Начать переписку' : 'Сообщений пока нет'))}</small></span>
            <span class="chat-dialog-meta">${dialog.created ? `<time>${chatTime(dialog.created, true)}</time>` : ''}
            ${dialog.count ? `<span class="chat-counter" aria-label="Непрочитанных: ${dialog.count}">${dialog.count}</span>` : ''}</span>`;
        button.onclick = () => chatSelect(dialog.peer);
        return button;
    });
    if (buttons.length) target.replaceChildren(...buttons);
    else target.textContent = query ? 'Сотрудники не найдены' : 'Диалогов пока нет';
}

async function refreshDialogs() {
    const actor = currentUser?.id;
    const target = $('#chatDialogs');
    if (!actor || !target) return;
    try {
        const result = await api('/chat/dialogs');
        if (currentUser?.id !== actor || $('#chatDialogs') !== target) return;
        chatDialogs = result.dialogs;
        renderDialogs();
        const dialog = chatDialogs.find(d => d.peer === chatPeer);
        if (chatPeer !== null && dialog) {
            $('#chatPresence').textContent = dialog.online ? 'В сети' : dialog.active ? '' : 'Сотрудник отключён';
        }
        showChatNavCount(chatDialogs.reduce((sum, dialog) => sum + dialog.count, 0));
    } catch (error) {
        if ($('#chatDialogs') === target && !chatDialogs.length) target.textContent = error.message;
    }
}

function chatSelect(peer) {
    chatPeer = peer;
    resetChatHistory();
    const dialog = chatDialogs.find(d => d.peer === peer);
    $('#chatList').hidden = peer !== null;
    $('#chatThread').hidden = peer === null;
    $('#chatBack').hidden = peer === null;
    $('#chatBell').hidden = peer !== null;
    $('#chatTitle').textContent = peer === null ? 'Мессенджер' : dialog?.name || 'Переписка';
    $('#chatPresence').textContent = dialog?.online ? 'В сети' : '';
    $('#chatForm').reset();
    $('#chatAttachment').hidden = true;
    $('#chatHistory').hidden = true;
    $('#chatMessages').textContent = 'Загрузка…';
    $('#chatMessages').renderedMessages = null;
    if (peer === null) refreshDialogs();
    else loadChat();
}

function chatTime(timestamp, withDate = false) {
    const date = new Date(timestamp);
    const zone = {timeZone: 'Europe/Moscow'};
    if (withDate && date.toLocaleDateString('sv-SE', zone) !== today()) {
        return date.toLocaleDateString('ru-RU', {...zone, day: '2-digit', month: '2-digit'});
    }
    return date.toLocaleTimeString('ru-RU', {...zone, hour: '2-digit', minute: '2-digit'});
}

function renderChatMessages(target, actor, older, initial) {
    const log = $('#chatLog');
    const messages = [...chatPages.values()].sort((a, b) => a.created - b.created || (a.id < b.id ? -1 : 1));
    let lastDay = '';
    const content = messages.map(message => {
        const mine = message.sender === actor;
        const day = new Date(message.created).toLocaleDateString('ru-RU', {timeZone: 'Europe/Moscow'});
        const date = day !== lastDay ? `<div class="chat-date">${day}</div>` : '';
        lastDay = day;
        const marker = message.id === unreadBoundary ? '<div class="chat-unread-line">Непрочитанные сообщения</div>' : '';
        const card = message.kind && db[message.kind]?.find(c => c.id === message.card_id);
        const openCard = message.kind === 'partners' ? 'partnerForm' : 'dealForm';
        const file = '/api/chat/files/' + encodeURIComponent(message.id);
        return `${date}${marker}<article class="chat-bubble ${mine ? 'mine' : ''}" data-message="${esc(message.id)}">
            ${!mine && !chatPeer ? `<strong>${esc(message.sender_name)}</strong>` : ''}
            ${message.body ? `<p class="chat-text">${esc(message.body)}</p>` : ''}
            ${message.filename && /\.(?:jpe?g|png|gif|webp)$/i.test(message.filename) ? `<img class="chat-photo" loading="lazy" src="${file}?preview=1" alt="${esc(message.filename)}">` : ''}
            ${message.filename ? `<a href="${file}">Скачать ${esc(message.filename)}</a>` : ''}
            ${card ? `<button type="button" onclick="${openCard}('${card.id}')">${esc(card.name)}</button>` : esc(message.title)}
            <small>${chatTime(message.created)}${mine ? (message.read_count ? ' · Прочитано' : ' · Отправлено') : ''}</small>
        </article>`;
    }).join('') || '<p>Сообщений пока нет</p>';
    if (target.renderedMessages === content) return;
    const oldHeight = log.scrollHeight;
    const atBottom = oldHeight - log.clientHeight - log.scrollTop < 40;
    target.innerHTML = content;
    target.renderedMessages = content;
    if (initial && unreadBoundary) {
        const boundary = target.querySelector('.chat-unread-line');
        if (boundary) log.scrollTop += boundary.getBoundingClientRect().top - log.getBoundingClientRect().top;
    } else if (older) log.scrollTop += log.scrollHeight - oldHeight;
    else if (initial || atBottom) log.scrollTop = log.scrollHeight;
}

async function loadChat(older = false) {
    const target = $('#chatMessages'), peer = chatPeer, actor = currentUser?.id;
    if (!target || !actor || peer === null || target.loadingOlder || (older && !chatBefore)) return;
    if (older) target.loadingOlder = true;
    const request = ++chatRequest;
    try {
        const query = new URLSearchParams({peer});
        if (older) query.set('before', chatBefore);
        const result = await api('/chat?' + query);
        if (request !== chatRequest || target !== $('#chatMessages') || currentUser?.id !== actor) return;
        const initial = target.renderedMessages == null;
        if (older) viewingHistory = true;
        if (older || !viewingHistory) chatBefore = result.before;
        if (!viewingHistory) chatPages.clear();
        for (const message of result.messages) chatPages.set(message.id, message);
        if (!unreadBoundary) unreadBoundary = result.messages.find(m => m.sender !== actor && !m.read_by_me)?.id || '';
        $('#chatHistory').hidden = !chatBefore;
        renderChatMessages(target, actor, older, initial);
        markVisibleChat();
    } catch (error) {
        if (request === chatRequest && target === $('#chatMessages')) {
            if (target.renderedMessages == null) target.textContent = error.message;
            else toast(error.message);
        }
    } finally {
        if (older) {
            target.loadingOlder = false;
            if (request !== chatRequest && target === $('#chatMessages') && chatPeer !== null) loadChat();
        }
    }
}

async function markVisibleChat() {
    const log = $('#chatLog');
    const actor = currentUser?.id;
    if (!log || log.closest('[hidden]') || !actor || chatReadBusy || document.hidden || $('#modal').open) return;
    const bounds = log.getBoundingClientRect();
    const ids = [...log.querySelectorAll('[data-message]')].filter(node => {
        const message = chatPages.get(node.dataset.message);
        if (!message || message.sender === actor || message.read_by_me) return false;
        const box = node.getBoundingClientRect();
        return Math.min(box.bottom, bounds.bottom, innerHeight) - Math.max(box.top, bounds.top, 0)
            >= Math.min(box.height, 32);
    }).slice(0, 200).map(node => node.dataset.message);
    if (!ids.length) return;
    chatReadBusy = true;
    const peer = chatPeer;
    try {
        await api('/chat/read', 'POST', {ids});
        if (currentUser?.id !== actor) return;
        if (chatPeer === peer) ids.forEach(id => {
            const message = chatPages.get(id);
            if (message) message.read_by_me = 1;
        });
        await refreshDialogs();
    } catch {
        // The next scroll or refresh retries receipts.
    } finally {
        chatReadBusy = false;
    }
}

function toggleChatAttachment() {
    const panel = $('#chatAttachment');
    panel.hidden = !panel.hidden;
}

async function sendChat(form) {
    const button = form.querySelector('[type=submit]');
    if (button.disabled || chatPeer === null) return;
    const payload = new FormData(form), peer = chatPeer;
    payload.set('peer', peer);
    const [kind, id] = payload.get('card').split(':');
    if (id) {
        payload.set('kind', kind);
        payload.set('card_id', id);
    }
    if (!payload.get('file')?.size) payload.delete('file');
    if (!payload.get('text').trim() && !payload.has('file') && !id) return;
    button.disabled = true;
    try {
        await multipart('/chat', payload);
        if ($('#chatForm') === form && chatPeer === peer) {
            form.reset();
            $('#chatAttachment').hidden = true;
            await loadChat();
            if (chatPeer === peer && $('#chatLog')) $('#chatLog').scrollTop = $('#chatLog').scrollHeight;
        }
        refreshDialogs();
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
    }
    backgroundBusy = true;
    try {
        const unread = await api('/chat/unread');
        if (currentUser?.id !== actor) return;
        const count = unread.conversations.reduce((sum, peer) => sum + peer.count, 0);
        showChatNavCount(count);
        if (count > previousUnread && (page !== 'Мессенджер' || document.hidden)) {
            toast('Новые сообщения · ' + count);
            if (window.Notification?.permission === 'granted') new Notification('Сообщения КСГ', {body: 'Непрочитанных: ' + count});
        }
        previousUnread = count;
        if (page === 'Мессенджер') await Promise.all([refreshDialogs(), loadChat()]);
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
