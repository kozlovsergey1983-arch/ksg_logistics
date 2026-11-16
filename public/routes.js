const routeEstimates=new Map(),routePending=new Map(),routeErrors=new Map();
let routeQueue=[],routeQueueActive=false,routeFormTimer=null,routeFormTicket=0;
function routeKey(item){return [String(item.from||'').trim().toLowerCase().replace(/\s+/g,' '),String(item.to||'').trim().toLowerCase().replace(/\s+/g,' '),item.fromLocation||'',item.toLocation||''].join('\n')}
function routeDistanceText(item){
 if(!hasSelectedCities(item))return 'Выберите города из подсказок';
 const value=routeEstimates.get(routeKey(item))?.distanceKm??item.distanceKm;
 return value===undefined||value===null||value===''?'Расчёт расстояния…':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:1})+' км по дороге';
}
function itemRouteInfo(item){
 if(!hasSelectedCities(item))return `<small>Нужно уточнить города</small><button type="button" class="route-map-button" onclick="itemForm('${item.id}')">Выбрать города</button>`;
 const error=routeErrors.get(routeKey(item));
 return `<small class="route-distance">${esc(error?'Не удалось рассчитать':routeDistanceText(item))}</small><button type="button" class="route-map-button" onclick="showItemRoute('${item.id}')">Карта</button>${error?` <button type="button" class="route-map-button" onclick="retryItemRoute('${item.id}')">Повторить расчёт</button>`:''}`;
}
async function estimateRoute(item){
 if(!hasSelectedCities(item))throw Error('Выбери оба города из подсказок');
 const key=routeKey(item);if(routeEstimates.has(key))return routeEstimates.get(key);
 if(routePending.has(key))return routePending.get(key);
 const pending=api('/routes/estimate','POST',{from:item.from,to:item.to,fromLocation:item.fromLocation,toLocation:item.toLocation}).then(result=>{routeEstimates.set(key,result);routeErrors.delete(key);return result}).finally(()=>routePending.delete(key));
 routePending.set(key,pending);return pending;
}
function queueRouteEstimates(items){
 for(const item of items){const key=routeKey(item);if(hasSelectedCities(item)&&!routeEstimates.has(key)&&!routeErrors.has(key)&&!routeQueue.some(i=>routeKey(i)===key))routeQueue.push({...item})}
 if(!routeQueueActive)processRouteQueue();
}
async function processRouteQueue(){
 if(!currentUser||!['Обзор','Грузы и перевозчики'].includes(page)){routeQueue=[];routeQueueActive=false;return}
 const item=routeQueue.shift();if(!item){routeQueueActive=false;return}routeQueueActive=true;
 try{await estimateRoute(item)}catch(e){if(e.status===429){routeQueue.unshift(item)}else routeErrors.set(routeKey(item),e.message)}
 if(currentUser&&['Обзор','Грузы и перевозчики'].includes(page))rows();
 setTimeout(processRouteQueue,2200);
}
function retryItemRoute(id){const item=db.items.find(i=>i.id===id);if(!item)return;routeErrors.delete(routeKey(item));queueRouteEstimates([item]);rows()}
function hasSelectedCities(item){return !!(item.fromLocation&&item.toLocation)}
function formRoute(form){return {from:form.elements.from.value,to:form.elements.to.value,fromLocation:form.elements.fromLocation.value,toLocation:form.elements.toLocation.value}}
function prepareRouteForm(item={}){
 const form=$('#modal form');if(!form)return;
 clearTimeout(routeFormTimer);routeFormTicket++;
 form.elements.distanceKm.readOnly=true;form.elements.distanceKm.value='';
 form.querySelector('#routeEstimateStatus').setAttribute('role','status');
 for(const name of ['from','to'])setupCityPicker(form,name,item[name+'Location']||'');
 form.dataset.routeKey='';
 if(hasSelectedCities(formRoute(form)))calculateFormRoute(form);
 else form.querySelector('#routeEstimateStatus').textContent='Выберите оба населённых пункта из подсказок. Затем расстояние рассчитается автоматически.';
}
function setupCityPicker(form,name,token){
 const input=form.elements[name],label=input.parentElement;
 const hidden=document.createElement('input');hidden.type='hidden';hidden.name=name+'Location';hidden.value=token;form.append(hidden);
 label.classList.add('city-picker');
 const list=document.createElement('div');list.id='city-list-'+name;list.className='city-options';list.setAttribute('role','listbox');list.hidden=true;label.append(list);
 input.autocomplete='off';input.setAttribute('aria-label',name==='from'?'Откуда':'Куда');input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-controls',list.id);input.setAttribute('aria-expanded','false');
 let timer=null,ticket=0,options=[],active=-1;
 const close=()=>{list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1};
 const message=text=>{list.replaceChildren();const p=document.createElement('div');p.className='city-message';p.textContent=text;list.append(p);list.hidden=false;input.setAttribute('aria-expanded','true')};
 const choose=option=>{ticket++;clearTimeout(timer);input.value=option.label;hidden.value=option.token;close();clearTimeout(routeFormTimer);routeFormTicket++;form.elements.distanceKm.value='';form.dataset.routeKey='';if(hasSelectedCities(formRoute(form)))calculateFormRoute(form);else form.querySelector('#routeEstimateStatus').textContent='Теперь выберите второй город из подсказок.'};
 const search=async(value,version)=>{
  if(!form.isConnected||!$('#modal').open||version!==ticket)return;
  message('Поиск населённых пунктов…');
  try{const result=await api('/places?query='+encodeURIComponent(value));if(version!==ticket||input.value.trim()!==value||!form.isConnected||!$('#modal').open)return;
   options=result.suggestions;active=-1;list.replaceChildren();
   if(!options.length)return message('Населённые пункты не найдены. Уточните название, страну или регион.');
   options.forEach((option,index)=>{const button=document.createElement('button');button.type='button';button.id=list.id+'-'+index;button.setAttribute('role','option');button.setAttribute('aria-selected','false');button.textContent=option.label;button.addEventListener('mousedown',event=>event.preventDefault());button.addEventListener('click',()=>choose(option));list.append(button)});
   const credit=document.createElement('small');credit.className='city-credit';credit.textContent='© OpenStreetMap / Photon';list.append(credit);list.hidden=false;input.setAttribute('aria-expanded','true');
  }catch(e){if(version!==ticket||!form.isConnected)return;message(e.status===429?'Поиск выполняется…':e.message);if(e.status===429)timer=setTimeout(()=>search(value,version),1300)}
 };
 input.addEventListener('input',()=>{clearTimeout(timer);ticket++;options=[];hidden.value='';routeFormTicket++;clearTimeout(routeFormTimer);form.dataset.routeKey='';form.elements.distanceKm.value='';form.querySelector('#routeEstimateStatus').textContent='Выберите города из подсказок — расстояние рассчитается автоматически.';const value=input.value.trim();if(value.length<2){close();return}const version=ticket;timer=setTimeout(()=>search(value,version),500)});
 input.addEventListener('focus',()=>{if(!hidden.value&&input.value.trim().length>=2){clearTimeout(timer);const version=++ticket;timer=setTimeout(()=>search(input.value.trim(),version),200)}});
 input.addEventListener('blur',()=>{clearTimeout(timer);ticket++;setTimeout(close,150)});
 input.addEventListener('keydown',event=>{
  if(event.key==='Escape'){ticket++;clearTimeout(timer);close();event.preventDefault();return}
  if(list.hidden||!options.length)return;
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();active=active<0?(event.key==='ArrowDown'?0:options.length-1):(active+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;const buttons=list.querySelectorAll('[role=option]');buttons.forEach((button,index)=>button.setAttribute('aria-selected',String(index===active)));input.setAttribute('aria-activedescendant',buttons[active].id);buttons[active].scrollIntoView({block:'nearest'})}
  if(event.key==='Enter'){event.preventDefault();if(active>=0)choose(options[active])}
 });
}
async function calculateFormRoute(form){
 const ticket=++routeFormTicket,item=formRoute(form),key=routeKey(item);
 if(!hasSelectedCities(item))return null;
 const status=form.querySelector('#routeEstimateStatus');status.textContent='Рассчитываем дорожный маршрут…';
 try{const result=await estimateRoute(item);if(ticket!==routeFormTicket||!form.isConnected||key!==routeKey(formRoute(form)))return null;
  form.elements.distanceKm.value=result.distanceKm;form.dataset.routeKey=key;status.textContent=`${result.start.label} → ${result.finish.label}. Расстояние ориентировочное; ограничения грузовиков не учтены.`;return result;
 }catch(e){if(ticket===routeFormTicket&&form.isConnected){form.elements.distanceKm.value='';status.textContent=e.status===429?'Расчёт выполняется, подождите…':e.message;if(e.status===429){clearTimeout(routeFormTimer);routeFormTimer=setTimeout(()=>{if(form.isConnected)calculateFormRoute(form)},2300)}}return null}
}
async function saveRoutedItem(form,id){
 if(busy)return toast('Дождись завершения сохранения');
 const button=form.querySelector('button.primary');if(button.disabled)return;
 button.disabled=true;clearTimeout(routeFormTimer);
 try{
  const original=values(form);if(!original.from||!original.to||!original.name)return toast('Укажи маршрут и компанию');
  if(!hasSelectedCities(original))return toast('Выбери оба города из выпадающих подсказок');
  if(form.dataset.routeKey!==routeKey(original))await calculateFormRoute(form);
  if(!form.isConnected)return;
  const v=values(form);if(form.dataset.routeKey!==routeKey(v))v.distanceKm='';
  const saved={...db.items.find(i=>i.id===id),...v,id:id||uid(),kind:v.kind==='Груз'?'cargo':'truck'};
  if(!saved.distanceKm&&saved.distanceKm!==0)toast('Заявка сохранится без расстояния. Расчёт повторится в списке');
  await put('items',saved);
 }finally{button.disabled=false}
}
async function showItemRoute(id){
 const item=db.items.find(i=>i.id===id);if(!item)return;
 if(!hasSelectedCities(item)){itemForm(id);return}
 let result;try{result=await estimateRoute(item)}catch(e){return toast(e.message)}
 const points=result?`${result.start.lat},${result.start.lon}~${result.finish.lat},${result.finish.lon}`:`${item.from}~${item.to}`;
 const query='rtext='+encodeURIComponent(points)+'&rtt=auto';
 show(`<div class="route-viewer"><h2>${esc(item.from)} → ${esc(item.to)}</h2><p>${esc(routeErrors.has(routeKey(item))?'Расстояние пока не рассчитано':routeDistanceText(item))}. Расстояние ориентировочное; ограничения грузовиков не учтены. Маршрут на карте может отличаться.</p><iframe src="https://yandex.ru/map-widget/v1/?${query}" title="Карта маршрута" loading="lazy" referrerpolicy="no-referrer"></iframe><div class="actions"><a class="link" href="https://yandex.ru/maps/?${query}" target="_blank" rel="noopener noreferrer">Открыть в Яндекс Картах ↗</a><button type="button" onclick="closeModal()">Закрыть</button></div></div>`);
}
