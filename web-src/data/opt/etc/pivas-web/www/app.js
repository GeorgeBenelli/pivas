'use strict';
const $ = id => document.getElementById(id);
let deviceState = null, catalog = null, state = null, botState = null, slotNames = null, nameSlot = 1, filter = 'all', busy = false, editing = null, configSlot = 1, toastTimer;
function slotName(n){return slotNames?.names?.[String(n)]||`Слот ${n}`;}
function slotLabel(n){const name=slotName(n);return name===`Слот ${n}`?name:`${n} · ${name}`;}
function renderSlotNames(){
  [1,2].forEach(n=>{
    $('slotTitle'+n).textContent=slotName(n);
    const filterButton=document.querySelector(`[data-filter="${n}"]`);
    filterButton.textContent=slotLabel(n);filterButton.title=slotLabel(n);
    const option=$('editSlot').querySelector(`[value="${n}"]`);option.textContent=slotLabel(n);
    document.querySelector(`[data-name="${n}"]`).setAttribute('aria-label','Переименовать '+slotLabel(n));
  });
}
async function loadSlotNames(){try{slotNames=await request('/api/slots');renderSlotNames();showError('slotNamesError','');}catch(error){showError('slotNamesError',error.message);}}
const el = (tag, cls, text) => {const n = document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n;};
function notify(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').hidden=true;},5000);}
function showError(id,text){$(id).textContent=text;$(id).hidden=!text;}
async function request(url, payload, raw=false){
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),175000);
  try{
    const options={cache:'no-store',signal:controller.signal};
    if(payload!==undefined){options.method='POST';options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(payload);}
    const response=await fetch(url,options);
    if(response.redirected&&new URL(response.url).pathname==='/login'){location.assign('/login');throw new Error('Сессия истекла');}
    if(!response.ok){let error;try{error=(await response.json()).error;}catch(_){error='Не удалось выполнить запрос';}throw new Error(error||'Ошибка '+response.status);}
    return raw?response:await response.json();
  }catch(error){if(error.name==='AbortError')throw new Error('Время ожидания истекло. Обновите состояние перед повтором.');throw error;}
  finally{clearTimeout(timer);}
}
function setBusy(value){busy=value;document.querySelectorAll('button,select').forEach(b=>{b.disabled=value;});if(!value&&!catalog){$('newGroup').disabled=true;$('addDomain').disabled=true;}if(!value&&!state)$('vpnToggle').disabled=true;updateSwapButton();updateBotButton();}
function updateBotButton(){const ready=botState?.token_set&&botState?.ids?.length;$('botToggle').disabled=busy||!botState||(!botState.running&&!ready);$('botToggle').textContent=botState?.running?'Остановить бота':'Включить бота';}
function renderBot(){if(!botState)return;$('botRunning').textContent=botState.running?'Работает':'Остановлен';$('botTokenStatus').textContent=botState.token_set?'Токен задан':'Токен не задан';$('botIds').value=botState.ids.join('\n');updateBotButton();}
async function loadBot(){try{botState=await request('/api/bot');showError('botError','');renderBot();}catch(error){showError('botError',error.message);$('botRunning').textContent='Не удалось загрузить';}}
async function loadDevices(refresh=false){
  try{
    const result=await request(refresh?'/api/devices?refresh=1':'/api/devices');
    if(!Array.isArray(result.devices)||!result.devices.every(d=>typeof d.name==='string'&&typeof d.mac==='string'&&typeof d.ip==='string'))throw new Error('Некорректный ответ Keenetic');
    deviceState=result;showError('deviceError','');renderDevices();
    return true;
  }catch(error){
    showError('deviceError',error.message);
    if(!deviceState){
      $('devicePreview').textContent='Без Pivas: список недоступен · обновить';
      $('deviceDisclosureCount').textContent='Ошибка загрузки';
      $('deviceEmpty').textContent='Не удалось получить устройства. Нажмите «Получить из Keenetic».';
    }
    return false;
  }
}
async function load(withDevices=false){
  // The device roster should render even if status or catalog fails to load.
  const deviceLoad=withDevices?loadDevices():Promise.resolve();
  const botLoad=withDevices?loadBot():Promise.resolve();
  const namesLoad=loadSlotNames();
  const [newState,newCatalog]=await Promise.all([request('/cgi-bin/state.sh'),request('/api/catalog')]);
  await namesLoad;
  state=newState;catalog=newCatalog;render();
  showError('error',catalog.drift.length?'Списки изменены вне панели. Есть расхождения с группами; изменения временно заблокированы.':'');
  await deviceLoad;
  await botLoad;
}
async function task(fn,errorId='error'){if(busy)return;setBusy(true);try{await fn();}catch(e){showError(errorId,e.message);}finally{setBusy(false);}}
async function mutate(payload){catalog=await request('/api/catalog',{...payload,revision:catalog.revision});await load();notify('Изменения сохранены');}
function routeSelect(slot,onchange,label){const s=el('select','route-select');s.setAttribute('aria-label',label);s.title=slotLabel(slot);[1,2].forEach(n=>{const o=el('option','',slotLabel(n));o.value=n;s.append(o);});s.value=slot;s.onchange=()=>task(async()=>{try{await onchange(Number(s.value));}catch(e){s.value=slot;throw e;}});return s;}
function button(text,cls,fn,label){const b=el('button',cls,text);if(label)b.setAttribute('aria-label',label);b.onclick=fn;return b;}
function matches(text,slot,enabled=true){const query=$('search').value.trim().toLocaleLowerCase();return(!query||text.toLocaleLowerCase().includes(query))&&(filter==='all'||filter==='off'&&!enabled||String(slot)===filter&&enabled);}
function confirmAction(title,text,fn,label='Удалить',errorId='error'){$('confirmTitle').textContent=title;$('confirmText').textContent=text;$('confirmOk').textContent=label;$('confirmOk').onclick=()=>{$('confirm').close();task(fn,errorId);};$('confirm').showModal();}
function updateSwapButton(){const same=Boolean(state?.slot2?.same_as_slot1);$('swapSlots').disabled=busy||!state?.slot1?.server||!state?.slot2?.server||same;$('swapSlots').title=same?'Добавьте отдельную ссылку в слот 2 для обмена':'';}
function render(){
  updateSwapButton();
  if(state){
    const active=state.vpn_enabled, healthy=state.xray&&state.route_ready;
    $('statusPill').textContent=active?(healthy?'Активно':'Нужна проверка'):'На паузе';
    $('vpnTitle').textContent=active?(healthy?'Сеть подключена':'Проверьте подключение'):'Подключение на паузе';
    $('vpnHint').textContent=active?'Домены из списков направляются через выбранный слот.':'Перехват трафика выключен. Группы и настройки сохранены.';
    $('vpnToggle').textContent=active?'Приостановить':'Включить';
    $('uptime').textContent='Роутер работает '+state.uptime;
    $('memory').textContent=`Память ${state.mem_used} / ${state.mem_total} МБ`;
    $('server1').textContent=state.slot1.server?[state.slot1.server,state.slot1.transport].filter(Boolean).join(' · '):'Добавьте ссылку';
    $('server2').textContent=state.slot2.same_as_slot1?'Ссылка слота 1':state.slot2.server?[state.slot2.server,state.slot2.transport].filter(Boolean).join(' · '):'Добавьте ссылку';
    $('slot2Mirror').hidden=!state.slot2.same_as_slot1;
  }
  if(!catalog)return;
  const counts={1:0,2:0};catalog.standalone.forEach(d=>counts[d.slot]++);catalog.groups.forEach(g=>{if(g.enabled)counts[g.slot]+=g.domains.length;});
  [1,2].forEach(n=>{$('count'+n).textContent=`${counts[n]} записей`;$('count'+n).title='Активные записи групп и отдельные домены';});
  $('totalBadge').textContent=catalog.groups.length+' групп';
  $('groups').replaceChildren();
  const groups=catalog.groups.filter(g=>matches(g.name+' '+g.domains.join(' '),g.slot,g.enabled));
  groups.forEach(g=>{
    const card=el('article','group-card'+(g.enabled?'':' off'));card.dataset.slot=g.slot;
    const top=el('div','group-top'),icon=el('span','group-icon',Array.from(g.name)[0].toLocaleUpperCase()),title=el('div');
    title.append(el('h3','group-title',g.name),el('div','muted',`${g.domains.length} записей · ${g.enabled?'Активна':'Выключена'}`));top.append(icon,title);card.append(top);
    const chips=el('div','domain-chips');g.domains.slice(0,4).forEach(d=>{const chip=el('span','chip',d);chip.title=d;chips.append(chip);});if(g.domains.length>4)chips.append(el('span','chip','+'+(g.domains.length-4)));card.append(chips);
    const route=el('div','group-route');route.append(routeSelect(g.slot,slot=>mutate({action:'move',id:g.id,slot}),'Маршрут группы '+g.name));
    const tools=el('div','group-tools');tools.append(button('Изменить','text-button',()=>openEditor(g),'Изменить группу '+g.name));route.append(tools);card.append(route);
    const more=el('div','group-more');more.append(button(g.enabled?'Приостановить группу':'Включить группу','text-button',()=>task(()=>mutate({action:'toggle',id:g.id,enabled:!g.enabled}))));
    more.append(button('Удалить','icon-action danger-text',()=>confirmAction('Удалить «'+g.name+'»?',`Группа и её ${g.domains.length} доменов будут удалены из списков VPN. Правила родительских доменов в других группах продолжат действовать.`,()=>mutate({action:'delete',id:g.id}))));card.append(more);$('groups').append(card);
  });
  $('groupEmpty').hidden=groups.length>0;
  if(catalog.groups.length){$('groupEmpty').querySelector('h3').textContent='Группы не найдены';$('groupEmpty').querySelector('p').textContent='Измените поисковый запрос или фильтр.';$('emptyCreate').hidden=true;}
  else{$('groupEmpty').querySelector('h3').textContent='У каждого сервиса — своя группа';$('groupEmpty').querySelector('p').textContent='Соберите домены одного сервиса и управляйте ими вместе.';$('emptyCreate').hidden=false;}
  $('singles').replaceChildren();const singles=catalog.standalone.filter(d=>matches(d.domain,d.slot));$('singleCount').textContent=catalog.standalone.length;
  singles.forEach(d=>{const row=el('div','domain-row');row.append(el('span','domain-name',d.domain));row.append(routeSelect(d.slot,slot=>mutate({action:'domain-add',domains:[d.domain],slot}),'Маршрут '+d.domain));row.append(button('×','icon-action',()=>confirmAction('Удалить запись?',d.domain+' будет удалена из списка VPN.',()=>mutate({action:'domain-delete',domains:[d.domain]})),'Удалить '+d.domain));$('singles').append(row);});
  $('singleEmpty').hidden=singles.length>0;$('singleEmpty').textContent=catalog.standalone.length?'По этому фильтру доменов нет.':'Здесь появятся домены, добавленные отдельно.';
}
function openEditor(group=null,single=false){if(!catalog||busy)return;editing={id:group?.id,single};$('editorTitle').textContent=single?'Добавить записи':group?'Изменить группу':'Новая группа';$('nameLabel').hidden=single;$('groupName').required=!single;$('groupName').value=group?.name||'';$('editSlot').value=group?.slot||'1';$('editDomains').value=group?.domains.join('\n')||'';showError('editError','');$('editHint').textContent=single?'Домены и IPv4-подсети можно позже объединить в группу.':group?'Удалённые записи исчезнут из списков VPN. Состояние группы сохранится.':'Введите домены или IPv4-подсети по одному в строке. IPv6-подсети текущая маршрутизация Pivas не поддерживает.';updateCount();$('editor').showModal();}
function updateCount(){$('domainCount').textContent=new Set($('editDomains').value.split(/[\s,;]+/).filter(Boolean)).size+' записей';}
$('editForm').onsubmit=async e=>{e.preventDefault();if(busy)return;setBusy(true);showError('editError','');try{await mutate({action:editing.single?'domain-add':editing.id?'update':'create',id:editing.id,name:$('groupName').value,slot:Number($('editSlot').value),domains:$('editDomains').value});$('editor').close();}catch(err){showError('editError',err.message);}finally{setBusy(false);}};
$('domainFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;if(file.size>100000){showError('editError','Файл должен быть меньше 100 КБ');return;}$('editDomains').value=await file.text();updateCount();e.target.value='';};
$('editDomains').oninput=updateCount;
$('newGroup').onclick=() =>openEditor();$('emptyCreate').onclick=()=>openEditor();$('addDomain').onclick=()=>openEditor(null,true);
$('search').oninput=render;document.querySelectorAll('[data-filter]').forEach(b=>{b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>{const selected=x===b;x.classList.toggle('selected',selected);x.setAttribute('aria-pressed',String(selected));});render();};});
$('refresh').onclick=()=>task(async()=>{await load(true);notify('Состояние обновлено');});
$('vpnToggle').onclick=()=>{if(!state)return;const fn=async()=>{await request('/api/control',{action:state.vpn_enabled?'stop':'start'});await load();notify('Состояние подключения обновлено');};if(state.vpn_enabled)confirmAction('Приостановить VPN?','Перехват трафика будет выключен. Группы и настройки сохранятся; транспорт бота останется запущен.',fn,'Приостановить');else task(fn);};
$('swapSlots').onclick=()=>task(async()=>{showError('error','');const result=await request('/api/control',{action:'swap'});await load();$('ping1').textContent='';$('ping2').textContent='';notify(result.changed?'Ссылки поменялись местами':'В обоих слотах уже одинаковые ссылки');});
function openConfig(slot){configSlot=slot;$('configTitle').textContent='Настройка: '+slotLabel(slot);$('vlessUrl').value='';showError('configError','');$('config').showModal();}
document.querySelectorAll('[data-config]').forEach(b=>b.onclick=()=>openConfig(Number(b.dataset.config)));
document.querySelectorAll('[data-name]').forEach(b=>b.onclick=()=>{
  if(busy||!slotNames)return;
  nameSlot=Number(b.dataset.name);$('slotNameTitle').textContent='Название слота '+nameSlot;
  $('slotNameInput').value=slotName(nameSlot);showError('slotNameError','');
  $('slotNameDialog').showModal();$('slotNameInput').select();
});
async function saveSlotName(action){
  await task(async()=>{
    slotNames=await request('/api/slots',{action,slot:nameSlot,name:$('slotNameInput').value,revision:slotNames.revision});
    $('slotNameDialog').close();renderSlotNames();render();notify('Название сохранено');
  },'slotNameError');
}
$('slotNameForm').onsubmit=e=>{e.preventDefault();saveSlotName('rename');};
$('resetSlotName').onclick=()=>saveSlotName('reset');
$('configForm').onsubmit=async e=>{e.preventDefault();if(busy)return;setBusy(true);showError('configError','');try{await request('/api/control',{action:'set',slot:configSlot,url:$('vlessUrl').value.trim()});$('vlessUrl').value='';$('config').close();await load();notify('Слот обновлён');}catch(err){showError('configError',err.message);}finally{setBusy(false);}};
$('rollback').onclick=()=>{$('config').close();confirmAction('Откатить '+slotLabel(configSlot)+'?','Будет восстановлена предыдущая конфигурация этого слота.',async()=>{await request('/api/control',{action:'rollback',slot:configSlot});await load();notify('Конфигурация восстановлена');},'Восстановить');};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('config').addEventListener('close',()=>{$('vlessUrl').value='';});
document.querySelectorAll('[data-ping]').forEach(b=>b.onclick=()=>task(async()=>{const n=b.dataset.ping;if(state&&!state.xray){$('ping'+n).textContent='Xray остановлен · включите Pivas';return;}$('ping'+n).textContent='Проверяем…';try{const result=await request('/cgi-bin/ping.sh?slot='+n),slot=result['slot'+n];$('ping'+n).textContent=slot?.ok?`Доступен · ${slot.e2e_ms} мс`:slot?.err==='no-proxy'?'Xray не отвечает':slot?.err==='timeout'?'Таймаут подключения':'Нет ответа';}catch(err){$('ping'+n).textContent='Ошибка проверки';throw err;}}));
async function diagnostics(){await task(async()=>{const buttons=[$('diagnostics'),$('diagNav')],labels=buttons.map(b=>b.textContent);buttons.forEach(b=>{b.textContent='Собираем… до 45 с';});try{const response=await request('/api/diagnostics',{},true),blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='pivas-diag.txt';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Отчёт скачан. В нём есть домены и адреса серверов.');}finally{buttons.forEach((b,i)=>{b.textContent=labels[i];});}});}
$('diagnostics').onclick=diagnostics;$('diagNav').onclick=diagnostics;

function renderDevices(){
  if(!deviceState)return;
  const selectedDevice=$('routeDevice').value;
  $('routeDevice').replaceChildren(el('option','','Обычное устройство'));$('routeDevice').firstChild.value='';
  deviceState.devices.forEach(d=>{const o=el('option','',d.name+(d.excluded?' · без Pivas':''));o.value=d.mac;$('routeDevice').append(o);});$('routeDevice').value=selectedDevice;
  const query=$('deviceSearch').value.trim().toLocaleLowerCase();
  const list=deviceState.devices.filter(d=>(d.name+' '+d.mac+' '+d.ip).toLocaleLowerCase().includes(query));
  const excluded=deviceState.devices.filter(d=>d.excluded);
  $('deviceCount').textContent=excluded.length+' / '+(deviceState.limit||10)+' без Pivas';
  $('deviceDisclosureCount').textContent=deviceState.devices.length+' устройств';
  const labels=excluded.slice(0,3).map(d=>d.name.length>25?d.name.slice(0,24)+'…':d.name);
  $('devicePreview').textContent=excluded.length?'Без Pivas: '+labels.join(', ')+(excluded.length>3?' и ещё '+(excluded.length-3):'')+' · показать список':'Без Pivas: нет устройств · показать список';
  $('deviceList').replaceChildren();
  $('deviceWarning').textContent=deviceState.warning||'';$('deviceWarning').hidden=!deviceState.warning;
  $('deviceEmpty').hidden=list.length>0;$('deviceEmpty').textContent=query?'Устройства не найдены.':'Список пуст. Подключите устройство к Keenetic и обновите список.';
  list.forEach(d=>{
    const row=el('div','device-row'+(d.excluded?' excluded':''));row.dataset.mac=d.mac;
    row.append(el('span','device-icon','▣'));
    const info=el('div','device-info'),name=el('div','device-name',d.name);
    if(d.is_current)name.append(el('span','device-current','Это устройство'));
    info.append(name,el('div','device-meta',(d.ip||'Нет текущего IP')+' · '+d.mac),el('div','small muted',(d.online?'В сети':'Не в сети')+(d.interface?' · '+d.interface:'')));row.append(info);
    const actions=el('div','device-state');actions.append(el('span','pill',d.excluded?'Без Pivas':'С Pivas'));
    const title=d.excluded?'Включить Pivas':'Без Pivas';
    const toggle=button(title,d.excluded?'primary device-toggle':'secondary device-toggle',()=>confirmAction(
      d.excluded?'Включить Pivas для «'+d.name+'»?':'Отключить Pivas для «'+d.name+'»?',
      d.excluded?'Для этого устройства снова будут действовать группы и списки доменов. При необходимости переподключите устройство.':'Устройство будет обходить VPN и DNS-перехват Pivas. Остальные устройства продолжат работать как раньше. Текущим соединениям может потребоваться переподключение.',
      async()=>{deviceState=await request('/api/devices',{revision:deviceState.revision,mac:d.mac,excluded:!d.excluded});showError('deviceError','');renderDevices();notify(d.excluded?'Pivas включён для устройства':'Исключение сохранено');},title,'deviceError'),title+' для '+d.name);
    toggle.setAttribute('aria-pressed',String(d.excluded));actions.append(toggle);row.append(actions);$('deviceList').append(row);
  });
}
$('deviceSearch').oninput=renderDevices;
$('devicePreview').onclick=()=>{$('deviceDisclosure').open=true;$('deviceSearch').focus();};
$('refreshDevices').onclick=()=>task(async()=>{if(await loadDevices(true))notify('Список устройств обновлён');},'deviceError');

$('botSaveToken').onclick=()=>task(async()=>{const token=$('botToken').value.trim();if(!token)throw new Error('Вставьте токен от @BotFather');botState=await request('/api/bot',{action:'token',token});$('botToken').value='';renderBot();notify('Токен бота сохранён');},'botError');
$('botSaveIds').onclick=()=>task(async()=>{botState=await request('/api/bot',{action:'ids',ids:$('botIds').value});renderBot();notify('Список Telegram ID сохранён');},'botError');
$('botToggle').onclick=()=>task(async()=>{botState=await request('/api/bot',{action:botState.running?'stop':'start'});renderBot();notify(botState.running?'Бот запущен':'Бот остановлен');},'botError');

task(()=>load(true));

function resultLines(id,lines){const box=$(id);box.replaceChildren();lines.forEach(t=>box.append(el('p','',t)));box.hidden=false;}
$('routeForm').onsubmit=e=>{e.preventDefault();task(async()=>{showError('routeError','');const r=await request('/api/explain',{domain:$('routeDomain').value,mac:$('routeDevice').value});resultLines('routeResult',[r.domain+' → '+r.route,r.reason,'Правило: '+(r.rule||'нет')+' · Группа: '+(r.group||'нет'),'DNS: '+r.dns,r.note]);},'routeError');};
let backupPreview=null,backupArchive=null;
function invalidateBackup(){backupPreview=null;backupArchive=null;$('backupRestore').hidden=true;$('backupSummary').hidden=true;}
$('backupPassword').oninput=invalidateBackup;$('backupFile').onchange=invalidateBackup;
function backupPassword(){const value=$('backupPassword').value;if(value.length<12)throw new Error('Пароль копии должен содержать не менее 12 символов');return value;}
$('backupExport').onclick=()=>task(async()=>{showError('backupError','');const r=await request('/api/backup',{action:'export',password:backupPassword()}),url=URL.createObjectURL(new Blob([JSON.stringify(r.archive)],{type:'application/json'})),a=el('a');a.href=url;a.download='pivas-backup.pivas';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);$('backupPassword').value='';invalidateBackup();notify('Зашифрованная копия скачана');},'backupError');
$('backupCheck').onclick=()=>task(async()=>{showError('backupError','');invalidateBackup();const file=$('backupFile').files[0];if(!file||file.size>6*1024*1024)throw new Error('Выберите файл копии размером до 6 МБ');backupArchive=JSON.parse(await file.text());backupPreview=await request('/api/backup',{action:'preview',password:backupPassword(),archive:backupArchive});const s=backupPreview.summary;resultLines('backupSummary',['Копия проверена: '+s.groups+' групп, '+s.domains+' активных записей, '+s.excluded+' исключённых устройств, '+s.slots+' слота.','Восстановление заменит эти настройки. Текущий режим паузы сохранится.']);$('backupRestore').hidden=false;},'backupError');
$('backupRestore').onclick=()=>{if(!backupPreview)return;confirmAction('Восстановить настройки из копии?','Будут заменены группы, списки, исключения, ссылки слотов и настройки DNS. Подключения могут прерваться.',async()=>{await request('/api/backup',{action:'restore',password:backupPassword(),archive:backupArchive,revision:backupPreview.revision});$('backupPassword').value='';invalidateBackup();await load(true);notify('Настройки восстановлены');},'Восстановить','backupError');};
const measurements={};
async function measure(label){showError('performanceError','');measurements[label]=await request('/api/performance',{label});const box=$('performanceResult');box.replaceChildren();box.hidden=false;for(const key of ['idle','load']){const r=measurements[key];if(!r)continue;box.append(el('h3','',key==='idle'?'В покое':'Под нагрузкой'));if(!r.available){box.append(el('p','',r.note));continue;}const m=r.memory_kib;box.append(el('p','',`CPU: ${r.cpu_percent}% · Доступно памяти: ${Math.round((m.MemAvailable??m.MemFree)/1024)} из ${Math.round(m.MemTotal/1024)} МБ`));const table=el('table');const head=el('tr');['Процесс','CPU, % ядра','RSS, МБ'].forEach(t=>head.append(el('th','',t)));table.append(head);r.processes.forEach(p=>{const row=el('tr');[p.name+' · '+p.pid,p.cpu_one_core_percent??'—',(p.rss_kib/1024).toFixed(1)].forEach(v=>row.append(el('td','',v)));table.append(row);});box.append(table,el('p','small muted',r.dns.map(d=>`DNS :${d.port}: ${d.ok?d.ms+' мс':'нет ответа'}`).join(' · ')),el('p','small muted',r.note));}}
$('measureIdle').onclick=()=>task(()=>measure('idle'),'performanceError');$('measureLoad').onclick=()=>task(()=>measure('load'),'performanceError');

// The navigation uses native disclosure/anchors, including keyboard control.
const navMore=$('navMore');
function selectNavigation(id){
  document.querySelectorAll('.main-nav a[href^="#"]').forEach(link=>{
    const active=link.hash==='#'+id;link.classList.toggle('active',active);
    if(active)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
  });
  navMore.classList.toggle('current',Boolean(navMore.querySelector('a.active')));
}
document.querySelectorAll('.main-nav a,.nav-popover button,.brand,.skip-link').forEach(link=>link.addEventListener('click',()=>{navMore.open=false;if(link.hash)selectNavigation(link.hash.slice(1));}));
document.addEventListener('click',event=>{if(!navMore.contains(event.target))navMore.open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&navMore.open){navMore.open=false;navMore.querySelector('summary').focus();}});
let navigationPending=false;
window.addEventListener('scroll',()=>{
  if(navigationPending)return;navigationPending=true;
  requestAnimationFrame(()=>{
    navigationPending=false;
    const threshold=innerWidth<=760?180:135;
    const sections=['overview','library','devices','telegram','routeCheck','backups','performance'];
    let current='overview';for(const id of sections){if($(id).getBoundingClientRect().top<=threshold)current=id;}
    selectNavigation(current);
  });
},{passive:true});
