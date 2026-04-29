// MiniApp Core Logic
const tg=window.Telegram.WebApp;tg.ready();tg.expand();
const B='/miniapp';
let U=null,plans=[],selPlan=null,cur='home';
let HAS_YK=false,HAS_SBP=false,HAS_CB=false,HAS_FK=false,STARS=1.5,BOT_UNAME='';

// Theme
const saved=localStorage.getItem('theme')||(tg.colorScheme==='light'?'light':'dark');
setTheme(saved);
function setTheme(t){document.body.className=t;localStorage.setItem('theme',t);const btn=document.getElementById('tbtn');if(btn)btn.textContent=t==='dark'?'🌙':'☀️';}
function toggleTheme(){setTheme(document.body.className==='dark'?'light':'dark');haptic('light');}

// Toast
function toast(msg,type){type=type||'success';const el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.className='toast on'+(type==='error'?' err':type==='info'?' info':'');setTimeout(()=>el.classList.remove('on'),2500);}

// Copy
function copyText(text,label){haptic('light');if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(()=>toast('✅ '+(label||'Скопировано')),()=>fallbackCopy(text,label));}else{fallbackCopy(text,label);}}
function fallbackCopy(text,label){const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();try{document.execCommand('copy');toast('✅ '+(label||'Скопировано'));}catch(e){toast('❌ Не удалось скопировать','error');}document.body.removeChild(ta);}

// Haptic
function haptic(type){if(window.Telegram?.WebApp?.HapticFeedback){const map={light:'impactLight',medium:'impactMedium',heavy:'impactHeavy',success:'notificationSuccess',error:'notificationError'};Telegram.WebApp.HapticFeedback[map[type]||'impactLight']?.();}}

// Cache
function cacheSet(k,d){try{localStorage.setItem('c_'+k,JSON.stringify({t:Date.now(),d}));}catch(e){}}
function cacheGet(k,max){try{const r=localStorage.getItem('c_'+k);if(!r)return null;const p=JSON.parse(r);if(Date.now()-p.t>max)return null;return p.d;}catch(e){return null;}}

// API
async function api(url,opts,retries){opts=opts||{};retries=retries||2;const fu=(url.startsWith('http')?'':B)+url;for(let i=0;i<=retries;i++){try{const r=await fetch(fu,{...opts,signal:(new AbortController()).signal});if(!r.ok){const t=await r.text();throw new Error('HTTP '+r.status+': '+t.substring(0,200));}return await r.json();}catch(e){if(i===retries)throw e;await new Promise(r=>setTimeout(r,1000*(i+1)));}}}
async function apiPost(url,body){return api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}

// Formatters
function fmtDate(iso){if(!iso)return'—';return new Date(iso).toLocaleDateString('ru-RU',{day:'numeric',month:'short',year:'numeric'});}
function fmtMoney(v){return Math.floor(v).toLocaleString('ru-RU')+' ₽';}
function fmtDays(d){return d+' '+(d===1?'день':d<5?'дня':'дней');}
function daysLeft(iso){if(!iso)return 0;return Math.max(0,Math.ceil((new Date(iso)-new Date())/86400000));}

// Nav
function go(t){haptic('light');document.querySelectorAll('.scr').forEach(s=>s.classList.remove('on'));document.querySelectorAll('.nb').forEach(b=>b.classList.remove('on'));document.getElementById('s-'+t).classList.add('on');document.getElementById('n-'+t).classList.add('on');cur=t;if(t==='subs')loadSubs();if(t==='buy')loadBuy();if(t==='profile')loadProfile();if(t==='admin')loadAdmin();if(t==='help')loadHelp();}

// Promo modal
function openPromo(){haptic('medium');document.getElementById('pm').classList.add('on');document.getElementById('pi').focus();}
function closePromo(){document.getElementById('pm').classList.remove('on');document.getElementById('pi').value='';}
async function applyPromo(){const c=document.getElementById('pi').value.trim();if(!c){toast('Введите промокод','error');return;}haptic('medium');try{const d=await apiPost('/promo/apply',{code:c});if(d.ok){haptic('success');toast(d.result.message);closePromo();if(cur==='profile')loadProfile();if(cur==='home')loadHome();}else{haptic('error');toast(d.error||'Неверный промокод','error');}}catch(e){haptic('error');toast('Ошибка: '+e.message,'error');}}

// Share
function shareRef(code){haptic('medium');const text='🔐 VPN\n\nРеферальный код: '+code+'\nПрисоединяйся!';if(tg.showPopup){tg.showPopup({title:'Поделиться',message:'Отправьте друзьям',buttons:[{id:'copy',type:'default',text:'Копировать'}]},(b)=>{if(b==='copy')copyText(text,'Скопировано');});}else{copyText(text,'Скопировано');}}

// ── Loaders ──
async function loadHome(){
  const el=document.getElementById('home-c');
  const c=cacheGet('home',30000);if(c){renderHome(c);return;}
  el.innerHTML='<div class="ld"><div class="ls"></div></div>';
  try{const d=await api('/profile');if(d.ok){cacheSet('home',d);renderHome(d);}else{el.innerHTML='<div class="em"><div class="ei">😕</div><div class="em-title">Ошибка загрузки</div><button class="btn bp" onclick="loadHome()">Повторить</button></div>';}}catch(e){el.innerHTML='<div class="em"><div class="ei">😕</div><div class="em-title">'+e.message+'</div><button class="btn bp" onclick="loadHome()">Повторить</button></div>';}
}

function renderHome(d){
  const u=d.user||{};const keys=d.active_keys||[];const total=d.active_keys.length+d.archive_keys.length;
  const hasKey=keys.length>0;const k=hasKey?keys[0]:null;const dl=k?daysLeft(k.expires_at):0;
  let h='<div class="hero"><div style="font-size:13px;font-weight:700;opacity:.9;margin-bottom:4px">👋 Добро пожаловать</div>';
  h+='<div style="font-size:20px;font-weight:800">'+escapeHtml(u.full_name||'Пользователь')+'</div>';
  if(hasKey){h+='<div style="margin-top:10px;font-size:13px;opacity:.85">'+k.name+' • '+fmtDays(dl)+' осталось</div>';}
  h+='</div>';
  h+='<div class="stats-grid"><div class="stat-item"><div class="stat-val">'+total+'</div><div class="stat-lbl">Подписок</div></div><div class="stat-item"><div class="stat-val">'+fmtMoney(u.balance||0)+'</div><div class="stat-lbl">Баланс</div></div></div>';
  h+='<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">⚡ Быстрые действия</div>';
  h+='<button class="pb" onclick="go(\'buy\')"><span class="pb-icon" style="background:rgba(0,212,170,.15);font-size:16px">💳</span><div><div style="font-size:14px;font-weight:700">Купить подписку</div><div style="font-size:12px;color:var(--hi)">Выберите подходящий план</div></div></button>';
  h+='<button class="pb" onclick="openPromo()"><span class="pb-icon" style="background:rgba(245,158,11,.15);font-size:16px">🎁</span><div><div style="font-size:14px;font-weight:700">Активировать промокод</div><div style="font-size:12px;color:var(--hi)">Получите бонусы</div></div></button>';
  if(hasKey){h+='<button class="pb" onclick="copyText(\''+escapeHtml(k.access_url||'')+'\',\'Ключ скопирован\')"><span class="pb-icon" style="background:rgba(14,165,233,.15);font-size:16px">📋</span><div><div style="font-size:14px;font-weight:700">Копировать ключ</div><div style="font-size:12px;color:var(--hi)">Быстрое подключение</div></div></button>';}
  h+='</div>';
  document.getElementById('home-c').innerHTML=h;
}

async function loadSubs(){
  const el=document.getElementById('subs-c');el.innerHTML='<div class="ld"><div class="ls"></div></div>';
  try{const d=await api('/profile');if(d.ok){renderSubs(d);}else{el.innerHTML='<div class="em"><div class="ei">🔑</div><div class="em-title">Нет подписок</div><div class="em-sub">Купите подписку, чтобы начать</div><button class="btn bp" onclick="go(\'buy\')">Купить</button></div>';}}catch(e){el.innerHTML='<div class="em"><div class="ei">😕</div><div class="em-title">Ошибка</div><button class="btn bp" onclick="loadSubs()">Повторить</button></div>';}
}

function renderSubs(d){
  const active=d.active_keys||[];const archive=d.archive_keys||[];let h='';
  if(active.length===0&&archive.length===0){h='<div class="em"><div class="ei" style="font-size:64px">🔑</div><div class="em-title">Нет подписок</div><div class="em-sub">Купите подписку, чтобы начать</div><button class="btn bp" onclick="go(\'buy\')">Купить</button></div>';}
  else{
    if(active.length>0){h+='<div class="sl2">✅ Активные</div>';for(const k of active){const dl=daysLeft(k.expires_at);h+='<div class="kc active-key"><div class="kn">'+escapeHtml(k.name)+'</div><div class="ke">Истекает: '+fmtDate(k.expires_at)+' • '+fmtDays(dl)+' осталось</div><div class="db"><div class="df" style="width:'+Math.min(100,Math.max(5,dl))+'%"></div></div><button class="ku" onclick="copyText(\''+escapeHtml(k.access_url)+'\',\'Ключ скопирован\')">'+escapeHtml(k.access_url)+'</button></div>';}}
    if(archive.length>0){h+='<div class="sl2">📁 Архив</div>';for(const k of archive){h+='<div class="kc"><div class="kn">'+escapeHtml(k.name)+'</div><div class="ke">Истек: '+fmtDate(k.expires_at)+'</div></div>';}}
  }
  document.getElementById('subs-c').innerHTML=h;
}

async function loadBuy(){
  const el=document.getElementById('buy-c');if(plans.length>0){renderBuy();return;}el.innerHTML='<div class="ld"><div class="ls"></div></div>';
  try{const d=await api('/plans');if(d.ok){plans=d.plans||[];renderBuy();}else{el.innerHTML='<div class="em">Ошибка загрузки планов<button class="btn bp" onclick="loadBuy()">Повторить</button></div>';}}catch(e){el.innerHTML='<div class="em">Ошибка: '+e.message+'<button class="btn bp" onclick="loadBuy()">Повторить</button></div>';}
}

function renderBuy(){
  const el=document.getElementById('buy-c');let h='<div class="sl2">📋 Доступные планы</div>';
  if(plans.length===0){h+='<div class="em">Нет доступных планов</div>';}
  for(const p of plans){const sel=selPlan===p.id?' sel':'';h+='<div class="pc'+sel+'" onclick="selectPlan('+p.id+')"><div class="pn">'+escapeHtml(p.name)+'</div><div class="pp">'+fmtMoney(p.price)+'</div><div class="pd">'+fmtDays(p.duration_days)+' • '+escapeHtml(p.description||'')+'</div></div>';}
  if(selPlan){const p=plans.find(x=>x.id===selPlan);if(p){h+='<div class="sl2">💳 Способ оплаты</div>';h+='<button class="btn bp" onclick="payBalance()">💰 Оплатить с баланса ('+fmtMoney(p.price)+')</button>';if(HAS_YK)h+='<button class="btn bo" onclick="payYK()" style="margin-top:8px">💳 Банковская карта</button>';if(HAS_SBP)h+='<button class="btn bo" onclick="paySBP()" style="margin-top:8px">🏦 СБП</button>';if(HAS_FK)h+='<button class="btn bo" onclick="payFK()" style="margin-top:8px">🌐 FreeKassa</button>';if(HAS_CB)h+='<button class="btn bo" onclick="payStars()" style="margin-top:8px">⭐ Stars ('+Math.ceil(p.price*STARS)+' ⭐)</button>';}}
  el.innerHTML=h;
}
function selectPlan(id){haptic('light');selPlan=id;renderBuy();}

async function payBalance(){
  if(!selPlan)return;haptic('medium');const p=plans.find(x=>x.id===selPlan);if(!p)return;
  try{const d=await apiPost('/pay/balance',{plan_id:selPlan});if(d.ok){haptic('success');toast('✅ Подписка активирована!');selPlan=null;go('subs');}else{haptic('error');toast(d.error||'Ошибка оплаты','error');}}catch(e){haptic('error');toast('Ошибка: '+e.message,'error');}
}
async function payYK(){
  if(!selPlan)return;haptic('medium');try{const d=await apiPost('/pay/yookassa',{plan_id:selPlan,bot_username:BOT_UNAME});if(d.ok&&d.confirm_url){tg.openInvoice?tg.openInvoice(d.confirm_url):window.open(d.confirm_url,'_blank');startPaymentCheck(d.payment_id);}else{toast(d.error||'Ошибка','error');}}catch(e){toast('Ошибка: '+e.message,'error');}
}
async function paySBP(){
  if(!selPlan)return;haptic('medium');try{const d=await apiPost('/pay/sbp',{plan_id:selPlan});if(d.ok&&d.confirm_url){tg.openInvoice?tg.openInvoice(d.confirm_url):window.open(d.confirm_url,'_blank');startPaymentCheck(d.payment_id);}else{toast(d.error||'Ошибка','error');}}catch(e){toast('Ошибка: '+e.message,'error');}
}
async function payFK(){
  if(!selPlan)return;haptic('medium');try{const d=await apiPost('/pay/freekassa',{plan_id:selPlan});if(d.ok&&d.pay_url){window.open(d.pay_url,'_blank');}else{toast(d.error||'Ошибка','error');}}catch(e){toast('Ошибка: '+e.message,'error');}
}
async function payStars(){
  if(!selPlan||!window.Telegram?.WebApp?.openInvoice)return;haptic('medium');const p=plans.find(x=>x.id===selPlan);const amt=Math.ceil((p.price||0)*STARS);try{const d=await apiPost('/pay/stars',{plan_id:selPlan,amount:amt});if(d.ok&&d.invoice_url){tg.openInvoice(d.invoice_url,(status)=>{if(status==='paid'){toast('✅ Оплачено!');go('subs');}else{toast('❌ Оплата отменена','error');}});}else{toast(d.error||'Ошибка','error');}}catch(e){toast('Ошибка: '+e.message,'error');}
}
function startPaymentCheck(pid){let checks=0;const iv=setInterval(async()=>{checks++;if(checks>60){clearInterval(iv);return;}try{const d=await api('/pay/check/'+pid);if(d.status==='succeeded'){clearInterval(iv);haptic('success');toast('✅ Оплачено!');go('subs');}else if(d.status==='failed'){clearInterval(iv);haptic('error');toast('❌ Оплата не прошла','error');}}catch(e){}},3000);}

async function loadProfile(){
  const el=document.getElementById('profile-c');el.innerHTML='<div class="ld"><div class="ls"></div></div>';
  try{const d=await api('/profile');if(d.ok){renderProfile(d);}else{el.innerHTML='<div class="em">Ошибка<button class="btn bp" onclick="loadProfile()">Повторить</button></div>';}}catch(e){el.innerHTML='<div class="em">Ошибка: '+e.message+'<button class="btn bp" onclick="loadProfile()">Повторить</button></div>';}
}

function renderProfile(d){
  const u=d.user||{};let h='<div class="card" style="text-align:center"><div class="av" style="margin:0 auto 12px">'+(u.full_name?u.full_name[0].toUpperCase():'👤')+'</div><div style="font-size:18px;font-weight:800">'+escapeHtml(u.full_name||'Пользователь')+'</div><div style="font-size:13px;color:var(--hi);margin-top:2px">@'+escapeHtml(u.username||'—')+'</div></div>';
  h+='<div class="card"><div class="sr"><span class="sr-label">💰 Баланс</span><span class="sr-val">'+fmtMoney(u.balance||0)+'</span></div><div class="sr"><span class="sr-label">👥 Рефералы</span><span class="sr-val">'+(u.referrals_count||0)+'</span></div><div class="sr"><span class="sr-label">🔑 Подписки</span><span class="sr-val">'+(d.active_keys||[]).length+'</span></div></div>';
  h+='<button class="btn bo" onclick="openPromo()">🎁 Активировать промокод</button>';
  if(u.referral_code){h+='<div style="margin-top:12px"><div class="sl2">🔗 Реферальная программа</div><div class="rb"><div class="rl">'+escapeHtml(u.referral_code)+'</div><button class="btn bp bs" style="margin-top:8px" onclick="shareRef(\''+escapeHtml(u.referral_code)+'\')">Поделиться</button></div></div>';}
  document.getElementById('profile-c').innerHTML=h;
}

async function loadAdmin(){
  const el=document.getElementById('admin-c');el.innerHTML='<div class="ld"><div class="ls"></div></div>';
  try{const d=await api('/admin/stats');if(d.ok){renderAdmin(d);}else{el.innerHTML='<div class="em">Нет доступа</div>';}}catch(e){el.innerHTML='<div class="em">Ошибка<button class="btn bp" onclick="loadAdmin()">Повторить</button></div>';}
}
function renderAdmin(d){let h='<div class="hero"><div style="font-size:13px;font-weight:700;opacity:.9">👑 Панель администратора</div></div>';h+='<div class="stats-grid"><div class="stat-item"><div class="stat-val">'+(d.total_users||0)+'</div><div class="stat-lbl">Пользователей</div></div><div class="stat-item"><div class="stat-val">'+(d.active_subs||0)+'</div><div class="stat-lbl">Активных подписок</div></div><div class="stat-item"><div class="stat-val">'+(d.new_today||0)+'</div><div class="stat-lbl">Новых сегодня</div></div><div class="stat-item"><div class="stat-val">'+fmtMoney(d.revenue||0)+'</div><div class="stat-lbl">Выручка</div></div></div>';document.getElementById('admin-c').innerHTML=h;}

async function loadHelp(){
  const el=document.getElementById('help-c');const c=cacheGet('help',600000);if(c){renderHelp(c);return;}el.innerHTML='<div class="ld"><div class="ls"></div></div>';
  try{const d=await api('/faq');if(d.ok){cacheSet('help',d);renderHelp(d);}else{el.innerHTML='<div class="em">Ошибка<button class="btn bp" onclick="loadHelp()">Повторить</button></div>';}}catch(e){el.innerHTML='<div class="em">Ошибка: '+e.message+'<button class="btn bp" onclick="loadHelp()">Повторить</button></div>';}
}
function renderHelp(d){
  let h='<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">📱 Гайд по подключению</div>';
  h+='<div class="guide-step"><div class="guide-num">1</div><div class="guide-text"><div class="guide-title">Скачайте приложение</div><div class="guide-desc">V2RayNG (Android), Shadowrocket (iOS) или V2RayN (Windows)</div></div></div>';
  h+='<div class="guide-step"><div class="guide-num">2</div><div class="guide-text"><div class="guide-title">Скопируйте ключ</div><div class="guide-desc">Перейдите в раздел «Подписки» и нажмите на ключ</div></div></div>';
  h+='<div class="guide-step"><div class="guide-num">3</div><div class="guide-text"><div class="guide-title">Импортируйте</div><div class="guide-desc">Вставьте ссылку в приложение или отсканируйте QR-код</div></div></div>';
  h+='</div>';
  if(d.about){h+='<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:8px">ℹ️ О сервисе</div><div style="font-size:13px;color:var(--hi);line-height:1.5">'+escapeHtml(d.about)+'</div></div>';}
  h+='<div class="sl2">❓ Частые вопросы</div>';
  for(const f of(d.faq||[])){h+='<div class="faq-item" onclick="this.classList.toggle(\'open\')"><div class="faq-q">'+escapeHtml(f.q)+'<span class="faq-icon">▼</span></div><div class="faq-a">'+escapeHtml(f.a)+'</div></div>';}
  document.getElementById('help-c').innerHTML=h;
}

// Server status (loaded on home)
async function loadServerStatus(){
  try{const d=await api('/servers/status');if(d.ok){renderServerStatus(d);}}catch(e){}}
function renderServerStatus(d){
  const el=document.getElementById('srv-status');if(!el)return;
  let h='<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><span class="chip chip-'+(d.overall==='operational'?'ok':'warn')+'">● '+(d.overall==='operational'?'Все системы работают':'Частичные проблемы')+'</span><span style="font-size:12px;color:var(--hi)">'+d.active_keys+' ключей активно</span></div>';
  for(const s of(d.servers||[])){const st=s.status==='online'?'ok':s.status==='degraded'?'warn':'danger';h+='<div class="srv-item"><div class="srv-dot '+st+'"></div><div><div class="srv-name">'+escapeHtml(s.name)+'</div><div class="srv-region">'+escapeHtml(s.region)+'</div></div><div class="srv-ping">'+s.ping+'ms</div></div>';}
  el.innerHTML=h;
}

function escapeHtml(t){if(!t)return'';return t.replace(/&/g,'&amp;').replace(/\x3C/g,'<').replace(/\x3E/g,'>').replace(/"/g,'"');}

// Init
async function init(){
  console.log('[MiniApp] init() started');
  if(!window.Telegram||!window.Telegram.WebApp){
    console.log('[MiniApp] Not in Telegram WebApp');
    document.getElementById('home-c').innerHTML='<div class="em"><div class="ei" style="font-size:64px">📱</div><div class="em-title">Откройте через Telegram</div><div class="em-sub">Приложение работает только внутри Telegram</div></div>';
    document.querySelectorAll('.nb').forEach(b=>b.style.display='none');
    return;
  }
  console.log('[MiniApp] Telegram.WebApp available');
  
  // Try initData first, fallback to initDataUnsafe
  let initData = tg.initData || '';
  let authMethod = 'initData';
  
  if(!initData || initData.length < 10){
    console.log('[MiniApp] initData empty or short ('+(initData?initData.length:0)+' chars), trying initDataUnsafe');
    if(tg.initDataUnsafe && tg.initDataUnsafe.user){
      authMethod = 'fallback';
    } else {
      console.log('[MiniApp] No auth data available');
      document.getElementById('home-c').innerHTML='<div class="em"><div class="ei" style="font-size:64px">🔐</div><div class="em-title">Ошибка авторизации</div><div class="em-sub">Перезапустите приложение из Telegram</div></div>';
      return;
    }
  }
  
  try{
    console.log('[MiniApp] Loading settings...');
    const sd=await api('/settings',{},3);
    if(sd.ok){HAS_YK=sd.has_yookassa||false;HAS_SBP=sd.has_sbp||false;HAS_CB=sd.has_cryptobot||false;HAS_FK=sd.has_freekassa||false;STARS=sd.stars_rate||1.5;BOT_UNAME=sd.bot_username||'';}
    console.log('[MiniApp] Settings loaded');
    
    let d;
    if(authMethod === 'initData'){
      console.log('[MiniApp] Auth via initData');
      d=await apiPost('/auth',{initData:initData});
    } else {
      console.log('[MiniApp] Auth via fallback (initDataUnsafe)');
      d=await apiPost('/auth-fallback',{user:tg.initDataUnsafe.user});
    }
    
    if(!d.ok){
      console.error('[MiniApp] Auth failed:', d.error, d.detail);
      document.getElementById('home-c').innerHTML='<div class="em"><div class="ei" style="font-size:64px">🔐</div><div class="em-title">Ошибка авторизации</div><div class="em-sub">'+(d.error||'Неизвестная ошибка')+'</div><div style="font-size:12px;color:var(--hi);margin-top:8px">Попробуйте перезапустить приложение</div></div>';
      return;
    }
    
    console.log('[MiniApp] Auth success, user:', d.user?.id);
    U=d.user||null;
    if(U&&U.is_admin){document.getElementById('n-admin').style.display='block';}
    
    loadHome();
    loadServerStatus();
    
    setInterval(()=>{
      if(cur==='home')loadHome();
      if(cur==='profile')loadProfile();
      if(cur==='admin')loadAdmin();
    },30000);
  }catch(e){
    console.error('[MiniApp] Init error:', e);
    document.getElementById('home-c').innerHTML='<div class="em"><div class="ei" style="font-size:64px">😕</div><div class="em-title">Ошибка подключения</div><div class="em-sub">'+e.message+'</div><button class="btn bp" onclick="init()">Повторить</button></div>';
  }
}

// Pull to refresh
let ptrStart=0,ptrPulling=false;
document.addEventListener('touchstart',e=>{if(window.scrollY<=0){ptrStart=e.touches[0].clientY;ptrPulling=true;}},{passive:true});
document.addEventListener('touchmove',e=>{if(!ptrPulling)return;const dy=e.touches[0].clientY-ptrStart;if(dy>80&&window.scrollY<=0){e.preventDefault();const el=document.getElementById('ptr');if(el)el.classList.add('show');}},{passive:false});
document.addEventListener('touchend',()=>{if(!ptrPulling)return;ptrPulling=false;const el=document.getElementById('ptr');if(el)el.classList.remove('show');if(cur==='home')loadHome();if(cur==='subs')loadSubs();if(cur==='buy')loadBuy();if(cur==='profile')loadProfile();if(cur==='help')loadHelp();if(cur==='admin')loadAdmin();toast('🔄 Обновлено','info');},{passive:true});

// Start
document.addEventListener('DOMContentLoaded',init);
