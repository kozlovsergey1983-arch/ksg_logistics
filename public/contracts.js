const contractStatuses = ['Черновик', 'На согласовании', 'Подписан', 'Аннулирован'];

async function contractsPage() {
    $('#app').innerHTML = heading('Договоры', 'Шаблоны Word, сформированные договоры и статусы',
        '<div class="actions"><button onclick="contractForm()">＋ Договор</button>' +
        (currentUser.role !== 'member' ? '<button onclick="templateUploadForm()">＋ Шаблон Word</button><button onclick="diadocPanel()">Диадок</button>' : '') +
        '</div>') + '<section class="panel" id="contractRegistry" aria-live="polite"></section>';
    await loadContractList($('#contractRegistry'), '');
}

async function loadContractList(target, filter) {
    try {
        const result = await api('/contracts' + filter);
        if (!target?.isConnected) return;
        target.innerHTML = result.contracts.map(c => {
            const status = currentUser.role !== 'member' ?
                `<select aria-label="Статус" onchange="contractStatus(this,'${c.id}','${c.status}')">${options(contractStatuses.map(s => [s, s]), c.status)}</select>` :
                `<span class="badge">${esc(c.status)}</span>`;
            return `<div class="row"><div><strong>${esc(c.number)} · ${esc(c.snapshot['partner.name'])}</strong>
                <p>${esc(c.snapshot.route)} ${esc(c.snapshot.rate)}</p>${status}</div>
                <div class="actions"><a href="/api/contracts/${c.id}/docx">DOCX ↓</a>
                <a href="/api/contracts/${c.id}/pdf">PDF ↓</a>${currentUser.role !== 'member' ?
                `<button onclick="diadocPanel('${c.message_id ? '' : c.id}','${esc(c.message_id || '')}','${esc(c.entity_id || '')}')">${c.message_id ? 'Статус в Диадоке' : 'Диадок'}</button>` : ''}</div></div>`;
        }).join('') || '<p>Договоров пока нет</p>';
    } catch (error) {
        if (target?.isConnected) target.textContent = error.message;
    }
}

function contractRegister(kind, id) {
    const partner = kind === 'partners' ? id : '';
    const deal = kind === 'deals' ? id : '';
    show(`<h2>Договоры</h2><div class="actions"><button onclick="contractForm('${partner}','${deal}')">＋ Договор</button>
        <button onclick="${partner ? 'partnerForm' : 'dealForm'}('${id}')">Назад к карточке</button></div>
        <div id="linkedContracts" aria-live="polite"></div>`);
    const target = $('#linkedContracts');
    target.dataset.filter = '?' + (partner ? 'partner=' + partner : 'deal=' + deal);
    loadContractList(target, target.dataset.filter);
}

async function contractStatus(select, id, expected) {
    select.disabled = true;
    try {
        await api('/contracts/' + id, 'PATCH', {status: select.value, expected});
        if ($('#linkedContracts')) {
            const filter = $('#linkedContracts').dataset.filter;
            await loadContractList($('#linkedContracts'), filter);
        } else {
            await contractsPage();
        }
    } catch (error) {
        select.value = expected;
        toast(error.message);
    } finally {
        select.disabled = false;
    }
}

function templateUploadForm() {
    form('Новый шаблон Word', field('name', 'Название') +
        '<label>Шаблон<input name="file" type="file" accept=".docx" required></label>' +
        '<p>Поля в Word оформляются как {{number}}, {{company.name}}, {{partner.inn}}, {{route}}, {{rate}}, {{cargo}} и {{payment}}.</p>',
    'uploadContractTemplate(this)');
}

async function uploadContractTemplate(form) {
    const button = form.querySelector('button.primary');
    button.disabled = true;
    try {
        await multipart('/contracts/templates', new FormData(form));
        closeModal();
        toast('Шаблон сохранён');
    } catch (error) {
        toast(error.message);
    } finally {
        button.disabled = false;
    }
}

async function contractForm(partner = '', deal = '') {
    try {
        const {templates} = await api('/contracts/templates');
        if (!templates.length) return toast('Попроси администратора загрузить шаблон Word');
        const selected = db.deals.find(d => d.id === deal);
        const parties = db.partners.filter(p => !p.archived && !p.deletionRequested).map(p => [p.id, p.name]);
        form('Сформировать договор',
            `<label>Шаблон<select name="template" required>${options(templates.map(t => [t.id, t.name]), '')}</select></label>
            <div><label>Контрагент<select name="partner" required onchange="refreshContractPartner(this.form)">${options([['', 'Выбери контрагента'], ...parties], partner || selected?.partnerId)}</select></label>
            <button type="button" data-contract-partner onclick="openContractPartner(this.form)">Открыть карточку</button>
            <small data-contract-inn aria-live="polite"></small></div>
            <label>Сделка<select name="deal" onchange="contractDealChanged(this.form)">${options([['', 'Без сделки'], ...db.deals.map(d => [d.id, d.name])], deal)}</select></label>` +
            field('route', 'Маршрут', selected?.name) + field('rate', 'Ставка', selected?.client) +
            field('cargo', 'Груз', selected?.cargo) + field('payment', 'Условия оплаты', selected?.details),
            'saveContract(this)');
        refreshContractPartner($('#modal form'));
    } catch (error) {
        toast(error.message);
    }
}

function refreshContractPartner(form) {
    const partner = db.partners.find(p => p.id === form.elements.partner.value);
    form.querySelector('[data-contract-partner]').disabled = !partner;
    form.querySelector('[data-contract-inn]').textContent = partner ?
        (partner.inn?.trim() ? 'ИНН ' + partner.inn : 'ИНН не указан') : '';
    return partner;
}

function openContractPartner(form, focusInn = false) {
    const partner = refreshContractPartner(form);
    if (!partner) return;
    const modal = $('#modal'), actor = currentUser?.id;
    const draft = [...modal.childNodes];
    partnerForm(partner.id, true);
    const editor = modal.querySelector('form');
    editor.querySelector('.actions button[type=button]').textContent = 'Назад к договору';
    modal.addEventListener('close', () => {
        if (modal.open || currentUser?.id !== actor) return;
        modal.replaceChildren(...draft);
        refreshContractPartner(form);
        modal.showModal();
        form.querySelector('[data-contract-partner]').focus();
    }, {once: true});
    if (focusInn) editor.elements.inn.focus();
}

function contractDealChanged(form) {
    const deal = db.deals.find(d => d.id === form.elements.deal.value);
    if (!deal) return;
    if (deal.partnerId) form.elements.partner.value = deal.partnerId;
    refreshContractPartner(form);
    for (const [field, value] of Object.entries({route: deal.name, rate: deal.client, cargo: deal.cargo, payment: deal.details})) {
        if (!form.elements[field].value) form.elements[field].value = value ?? '';
    }
}

async function saveContract(form) {
    const partner = refreshContractPartner(form);
    if (partner && !partner.inn?.trim()) {
        toast('Укажи ИНН в карточке контрагента');
        openContractPartner(form, true);
        return;
    }
    const button = form.querySelector('button.primary');
    button.disabled = true;
    try {
        const result = await api('/contracts', 'POST', values(form));
        closeModal();
        go('Договоры');
        toast('Создан договор ' + result.number);
    } catch (error) {
        toast(error.message);
    } finally {
        button.disabled = false;
    }
}

async function diadocPanel(cid = '', mid = '', eid = '') {
    try {
        const status = await api('/diadoc');
        if (!status.enabled) return show('<h2>Контур.Диадок</h2><p>Подключение API ещё не настроено.</p><button onclick="closeModal()">Закрыть</button>');
        show(`<h2>Контур.Диадок</h2>
        <form onsubmit="diadocQuery(this,'organizations');return false">${field('inn', 'ИНН')}${field('kpp', 'КПП')}<button>Найти организацию</button></form>
        <form onsubmit="diadocInvite(this);return false">${field('box', 'Ящик получателя')}${field('message', 'Сообщение')}<button>Пригласить</button></form>
        <form onsubmit="diadocQuery(this,'invitations');return false">${field('task', 'Идентификатор приглашения')}<button>Статус приглашения</button></form>
        <form onsubmit="diadocQuery(this,'documents');return false">
        <label>Направление<select name="direction"><option value="Inbound">Входящие</option><option value="Outbound">Исходящие</option></select></label>
        ${field('after', 'Ключ следующей страницы')}<button>Найти документы</button></form>
        <form onsubmit="diadocQuery(this,'status');return false">${field('messageId', 'Идентификатор сообщения', mid)}${field('entityId', 'Идентификатор документа', eid)}
        <button>Статус документа</button> <button type="button" onclick="diadocDownload(this.form)">Скачать документ</button></form>
        ${cid && status.signer ? `<form onsubmit="diadocSend(this,'${cid}');return false">${field('box', 'Ящик получателя')}
            <label>Отделённая подпись к PDF<input name="signature" type="file" accept=".sig,.p7s" required></label>
            <button class="primary">Отправить подписанный PDF</button></form>` : ''}
        <pre id="diadocResult" aria-live="polite"></pre><button onclick="closeModal()">Закрыть</button>`);
        if (mid && eid) diadocResult(() => api(diadocPath({messageId: mid, entityId: eid})));
    } catch (error) {
        toast(error.message);
    }
}

function diadocPath(data) {
    return '/diadoc/documents/' + encodeURIComponent(data.messageId) + '/' + encodeURIComponent(data.entityId);
}

async function diadocResult(call) {
    const target = $('#diadocResult');
    try {
        target.textContent = 'Запрос…';
        target.textContent = JSON.stringify(await call(), null, 2);
    } catch (error) {
        target.textContent = error.message;
    }
}

function diadocQuery(form, type) {
    const data = values(form);
    const path = type === 'status' ? diadocPath(data) : type === 'invitations' ?
        '/diadoc/invitations/' + encodeURIComponent(data.task) :
        '/diadoc/' + type + '?' + new URLSearchParams(data);
    diadocResult(() => api(path));
}

function diadocInvite(form) {
    if (confirm('Отправить приглашение в Диадоке?')) diadocResult(() => api('/diadoc/invitations', 'POST', values(form)));
}

function diadocDownload(form) {
    const data = values(form);
    if (data.messageId && data.entityId) window.location.assign('/api' + diadocPath(data) + '/file');
}

async function diadocSend(form, cid) {
    const signature = form.elements.signature.files[0];
    if (!signature?.size || signature.size > 192000) return toast('Выбери файл подписи до 192 КБ');
    if (!confirm('Отправить подписанный PDF выбранному получателю в Диадоке?')) return;
    const button = form.querySelector('button');
    button.disabled = true;
    try {
        const bytes = new Uint8Array(await signature.arrayBuffer());
        let raw = '';
        for (const byte of bytes) raw += String.fromCharCode(byte);
        await diadocResult(() => api('/diadoc/contracts/' + cid, 'POST', {box: form.elements.box.value.trim(), signature: btoa(raw)}));
    } finally {
        button.disabled = false;
    }
}
