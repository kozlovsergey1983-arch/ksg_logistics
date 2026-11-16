const routeEstimates=new Map(),routePending=new Map(),routeErrors=new Map();
let routeQueue=[],routeQueueActive=false,routeFormTimer=null,routeFormTicket=0;
function routeKey(item){return [item.from,item.to].map(v=>String(v||'').trim().toLowerCase().replace(/\s+/g,' ')).join('\n')}
function routeDistanceText(item){
 const value=routeEstimates.get(routeKey(item))?.distanceKm??item.distanceKm;
 return value===undefined||value===null||value===''?'Расчёт расстояния…':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:1})+' км по дороге';
}
function itemRouteInfo(item){
 const error=routeErrors.get(routeKey(item));
 return `<small class="route-distance">${esc(error?'Не удалось рассчитать':routeDistanceText(item))}</small><button type="button" class="route-map-button" onclick="showItemRoute('${item.id}')">Карта</button>${error?` <button type="button" class="route-map-button" onclick="retryItemRoute('${item.id}')">Повторить расчёт</button>`:''}`;
}
async function estimateRoute(item){
 const key=routeKey(item);if(routeEstimates.has(key))return routeEstimates.get(key);
 if(routePending.has(key))return routePending.get(key);
 const pending=api('/routes/estimate','POST',{from:item.from,to:item.to}).then(result=>{routeEstimates.set(key,result);routeErrors.delete(key);return result}).finally(()=>routePending.delete(key));
 routePending.set(key,pending);return pending;
}
function queueRouteEstimates(items){
 for(const item of items){const key=routeKey(item);if(item.from&&item.to&&!routeEstimates.has(key)&&!routeErrors.has(key)&&!routeQueue.some(i=>routeKey(i)===key))routeQueue.push({id:item.id,from:item.from,to:item.to})}
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
function prepareRouteForm(){
 const form=$('#modal form');if(!form)return;
 form.elements.distanceKm.readOnly=true;
 const recalculate=()=>{clearTimeout(routeFormTimer);routeFormTicket++;form.elements.distanceKm.value='';form.dataset.routeKey='';$('#routeEstimateStatus').textContent='Расстояние рассчитывается автоматически по указанным городам.';if(form.elements.from.value.trim().length>=2&&form.elements.to.value.trim().length>=2)routeFormTimer=setTimeout(()=>calculateFormRoute(form),800)};
 form.elements.from.addEventListener('input',recalculate);form.elements.to.addEventListener('input',recalculate);
 if(form.elements.from.value.trim().length>=2&&form.elements.to.value.trim().length>=2)calculateFormRoute(form);
}
async function calculateFormRoute(form){
 const ticket=++routeFormTicket,item={from:form.elements.from.value,to:form.elements.to.value},key=routeKey(item);
 const status=form.querySelector('#routeEstimateStatus');status.textContent='Рассчитываем дорожный маршрут…';
 try{const result=await estimateRoute(item);if(ticket!==routeFormTicket||!form.isConnected||key!==routeKey({from:form.elements.from.value,to:form.elements.to.value}))return null;
  form.elements.distanceKm.value=result.distanceKm;form.dataset.routeKey=key;status.textContent=`${result.start.label} → ${result.finish.label}. Расстояние ориентировочное; ограничения грузовиков не учтены.`;return result;
 }catch(e){if(ticket===routeFormTicket&&form.isConnected){form.elements.distanceKm.value='';status.textContent=e.status===429?'Расчёт выполняется, подождите…':e.message;if(e.status===429){clearTimeout(routeFormTimer);routeFormTimer=setTimeout(()=>{if(form.isConnected)calculateFormRoute(form)},2300)}}return null}
}
async function saveRoutedItem(form,id){
 if(busy)return toast('Дождись завершения сохранения');
 const button=form.querySelector('button.primary');if(button.disabled)return;
 button.disabled=true;clearTimeout(routeFormTimer);
 try{
  const original=values(form);if(!original.from||!original.to||!original.name)return toast('Укажи маршрут и компанию');
  if(form.dataset.routeKey!==routeKey(original))await calculateFormRoute(form);
  if(!form.isConnected)return;
  const v=values(form);if(form.dataset.routeKey!==routeKey(v))v.distanceKm='';
  const saved={...db.items.find(i=>i.id===id),...v,id:id||uid(),kind:v.kind==='Груз'?'cargo':'truck'};
  if(!saved.distanceKm&&saved.distanceKm!==0)toast('Заявка сохранится без расстояния. Расчёт повторится в списке');
  await put('items',saved);
 }finally{button.disabled=false}
}
function showItemRoute(id){
 const item=db.items.find(i=>i.id===id);if(!item)return;
 const result=routeEstimates.get(routeKey(item));
 const points=result?`${result.start.lat},${result.start.lon}~${result.finish.lat},${result.finish.lon}`:`${item.from}~${item.to}`;
 const query='rtext='+encodeURIComponent(points)+'&rtt=auto';
 show(`<div class="route-viewer"><h2>${esc(item.from)} → ${esc(item.to)}</h2><p>${esc(routeErrors.has(routeKey(item))?'Расстояние пока не рассчитано':routeDistanceText(item))}. Расстояние ориентировочное; ограничения грузовиков не учтены. Маршрут на карте может отличаться.</p><iframe src="https://yandex.ru/map-widget/v1/?${query}" title="Карта маршрута" loading="lazy" referrerpolicy="no-referrer"></iframe><div class="actions"><a class="link" href="https://yandex.ru/maps/?${query}" target="_blank" rel="noopener noreferrer">Открыть в Яндекс Картах ↗</a><button type="button" onclick="closeModal()">Закрыть</button></div></div>`);
}
