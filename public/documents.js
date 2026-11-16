let documentsCache=[],documentsRevision=0,documentsRequest=0;
function documentsAdmin(){return currentUser&&['owner','admin'].includes(currentUser.role)}
function documentsShell(){return heading('Документы','Общие файлы команды. Файлы сотрудников публикуются после подтверждения администратора.',addbtn(documentsAdmin()?'Добавить документ':'Предложить документ','documentUploadForm()'))+'<div id="documentsContent" aria-live="polite">Загрузка документов…</div>'}
async function loadDocuments(){
 const ticket=++documentsRequest;
 try{const r=await api('/documents');if(page!=='Документы'||ticket!==documentsRequest||!currentUser)return;documentsCache=r.documents;documentsRevision=r.revision;renderDocuments()}
 catch(e){if(page==='Документы'&&ticket===documentsRequest&&$('#documentsContent'))$('#documentsContent').innerHTML=`<p role="alert">${esc(e.message)}</p><button onclick="loadDocuments()">Повторить</button>`}
}
function renderDocuments(){
 const approved=documentsCache.filter(d=>d.status==='approved'),pending=documentsCache.filter(d=>d.status==='pending'),rejected=documentsCache.filter(d=>d.status==='rejected');
 $('#documentsContent').innerHTML=`<section class="panel"><h2>Опубликованные документы</h2>${documentsAdmin()?'<p>Используйте ↑ и ↓, чтобы изменить порядок для всех сотрудников.</p>':''}${approved.map((d,i)=>documentRow(d,i,approved.length)).join('')||'<p>Пока нет опубликованных документов.</p>'}</section><section class="panel"><h2>${documentsAdmin()?'Ожидают подтверждения':'Мои файлы на подтверждении'}</h2>${pending.map(d=>documentRow(d)).join('')||'<p>Нет файлов на подтверждении.</p>'}</section>${rejected.length?`<section class="panel"><h2>${documentsAdmin()?'Отклонённые документы':'Мои отклонённые файлы'}</h2>${rejected.map(d=>documentRow(d)).join('')}</section>`:''}`;
}
function documentRow(d,index=0,count=0){
 const admin=documentsAdmin();
 return `<div class="row"><div><strong>${esc(d.name)}</strong><small>${esc(d.uploader_name)} · ${Math.ceil(d.size/1024)} КБ · ${new Date(d.uploaded*1000).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})}</small>${d.status==='pending'?'<span class="badge amber">На подтверждении</span>':''}${d.status==='rejected'?`<span class="badge gray">Отклонён</span><p>${esc(d.reason||'Причина не указана')}</p>`:''}</div><div class="actions"><a class="link" href="/api/documents/${encodeURIComponent(d.id)}/download">Скачать</a>${admin&&d.status==='approved'?`<button type="button" aria-label="Переместить выше" ${index===0?'disabled':''} onclick="moveDocument('${d.id}',-1)">↑</button><button type="button" aria-label="Переместить ниже" ${index===count-1?'disabled':''} onclick="moveDocument('${d.id}',1)">↓</button>`:''}${admin&&d.status==='pending'?`<button type="button" class="primary" onclick="moderateDocument('${d.id}','approve')">Подтвердить</button><button type="button" onclick="moderateDocument('${d.id}','reject')">Отклонить</button>`:''}${admin?`<button type="button" onclick="deleteDocument('${d.id}')">Удалить</button>`:''}</div></div>`;
}
function documentUploadForm(){
 show(`<h2>${documentsAdmin()?'Добавить документ':'Предложить документ'}</h2><p>${documentsAdmin()?'Файл сразу появится в общем списке.':'Файл увидят администраторы. В общем списке он появится после подтверждения.'} Размер — до 4 МБ.</p><form id="documentUpload"><label>Файл<input name="file" type="file" required></label><p id="documentError" role="alert"></p><div class="actions"><button type="button" onclick="closeModal()">Отмена</button><button class="primary">${documentsAdmin()?'Опубликовать':'Отправить на подтверждение'}</button></div></form>`);
 const form=$('#documentUpload');
 form.onsubmit=async event=>{
  event.preventDefault();if(busy)return toast('Дождись завершения предыдущего действия');
  const file=form.file.files[0];if(!file)return;
  if(!file.size||file.size>4*1024*1024){$('#documentError').textContent='Размер файла: от 1 байта до 4 МБ';return}
  const body=new FormData();body.append('file',file);body.append('revision',documentsRevision);
  busy=true;form.querySelector('.primary').disabled=true;$('#documentError').textContent='';
  try{
   const response=await fetch('/api/documents',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':csrf},body});
   const r=await response.json();if(!response.ok){const e=Error(r.error||'Ошибка загрузки');e.status=response.status;throw e}
   closeModal();await loadDocuments();toast(r.status==='approved'?'Документ опубликован':'Файл отправлен администратору');
  }catch(e){$('#documentError').textContent=e.message;if(e.status===409)await loadDocuments()}
  finally{busy=false;form.querySelector('.primary').disabled=false}
 };
}
async function documentAction(path,method,data){
 if(busy)return toast('Дождись завершения предыдущего действия');busy=true;
 try{await api(path,method,{...data,revision:documentsRevision});await loadDocuments();toast('Изменения сохранены')}
 catch(e){toast(e.message);if(e.status===409)await loadDocuments()}
 finally{busy=false}
}
function moderateDocument(id,action){
 if(busy)return;let reason='';if(action==='reject'){reason=prompt('Причина отклонения (до 1000 символов):','');if(reason===null)return;if(reason.length>1000)return toast('Причина: не больше 1000 символов')}
 return documentAction('/documents/'+encodeURIComponent(id)+'/moderate','POST',{action,reason});
}
function deleteDocument(id){
 if(busy)return;const doc=documentsCache.find(d=>d.id===id);if(!doc)return;
 if(confirm(`Удалить файл «${doc.name}»? Это действие нельзя отменить.`))return documentAction('/documents/'+encodeURIComponent(id),'DELETE',{});
}
function moveDocument(id,direction){
 const ids=documentsCache.filter(d=>d.status==='approved').map(d=>d.id),from=ids.indexOf(id),to=from+direction;
 if(from<0||to<0||to>=ids.length)return;[ids[from],ids[to]]=[ids[to],ids[from]];
 return documentAction('/documents/order','PUT',{ids});
}
