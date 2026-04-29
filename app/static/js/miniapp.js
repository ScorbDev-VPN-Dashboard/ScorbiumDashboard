// MiniApp Core Logic
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();
const B = '/app';
let U = null, plans = [], selPlan = null, cur = 'home';
let HAS_YK = false, HAS_SBP = false, HAS_CB = false, HAS_FK = false, HAS_STARS = false, STARS = 1.5, BOT_UNAME = '';
let GLOBAL_INIT_DATA = tg.initData || '';

// ── Helpers ──────────────────────────────────────────────────
function escapeHtml(text) {
    if (text == null) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '<')
        .replace(/>/g, '>')
        .replace(/"/g, '"')
        .replace(/'/g, '&#039;');
}

const saved = localStorage.getItem('theme') || (tg.colorScheme === 'light' ? 'light' : 'dark');
setTheme(saved);

function setTheme(t) {
    document.body.className = t;
    localStorage.setItem('theme', t);
    const btn = document.getElementById('tbtn');
    if (btn) btn.textContent = t === 'dark' ? '🌙' : '☀️';
}

function toggleTheme() {
    setTheme(document.body.className === 'dark' ? 'light' : 'dark');
    haptic('light');
}

// Toast
function toast(msg, type) {
    type = type || 'success';
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast on' + (type === 'error' ? ' err' : type === 'info' ? ' info' : '');
    setTimeout(() => el.classList.remove('on'), 2500);
}

// Copy
function copyText(text, label) {
    haptic('light');
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => toast('✅ ' + (label || 'Скопировано')), () => fallbackCopy(text, label));
    } else {
        fallbackCopy(text, label);
    }
}

function fallbackCopy(text, label) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
        document.execCommand('copy');
        toast('✅ ' + (label || 'Скопировано'));
    } catch (e) {
        toast('❌ Не удалось скопировать', 'error');
    }
    document.body.removeChild(ta);
}

// Haptic
function haptic(type) {
    if (window.Telegram?.WebApp?.HapticFeedback) {
        const map = { light: 'impactLight', medium: 'impactMedium', heavy: 'impactHeavy', success: 'notificationSuccess', error: 'notificationError' };
        Telegram.WebApp.HapticFeedback[map[type] || 'impactLight']?.();
    }
}

// Cache
function cacheSet(k, d) {
    try { localStorage.setItem('c_' + k, JSON.stringify({ t: Date.now(), d })); } catch (e) { }
}

function cacheGet(k, max) {
    try {
        const r = localStorage.getItem('c_' + k);
        if (!r) return null;
        const p = JSON.parse(r);
        if (Date.now() - p.t > max) return null;
        return p.d;
    } catch (e) { return null; }
}

// API
async function api(url, opts, retries) {
    opts = opts || {};
    retries = retries || 2;
    const fu = (url.startsWith('http') ? '' : B) + url;
    const headers = { 'Content-Type': 'application/json' };
    if (GLOBAL_INIT_DATA) { headers['X-Telegram-Init-Data'] = GLOBAL_INIT_DATA; }
    if (opts.headers) { Object.assign(headers, opts.headers); }
    for (let i = 0; i <= retries; i++) {
        try {
            const r = await fetch(fu, { ...opts, headers, signal: (new AbortController()).signal });
            if (!r.ok) {
                const t = await r.text();
                throw new Error('HTTP ' + r.status + ': ' + t.substring(0, 200));
            }
            return await r.json();
        } catch (e) {
            if (i === retries) throw e;
            await new Promise(r => setTimeout(r, 1000 * (i + 1)));
        }
    }
}

async function apiPost(url, body) {
    body = body || {};
    if (GLOBAL_INIT_DATA && typeof body === 'object') { body.initData = GLOBAL_INIT_DATA; }
    return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

// Formatters
function fmtDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function fmtMoney(v) {
    return Math.floor(v).toLocaleString('ru-RU') + ' ₽';
}

function fmtDays(d) {
    return d + ' ' + (d === 1 ? 'день' : d < 5 ? 'дня' : 'дней');
}

function daysLeft(iso) {
    if (!iso) return 0;
    return Math.max(0, Math.ceil((new Date(iso) - new Date()) / 86400000));
}

// Nav
function go(t) {
    haptic('light');
    document.querySelectorAll('.scr').forEach(s => s.classList.remove('on'));
    document.querySelectorAll('.nb').forEach(b => b.classList.remove('on'));
    const sEl = document.getElementById('s-' + t);
    const nEl = document.getElementById('n-' + t);
    if (sEl) sEl.classList.add('on');
    if (nEl) nEl.classList.add('on');
    cur = t;
    if (t === 'subs') loadSubs();
    if (t === 'buy') loadBuy();
    if (t === 'profile') loadProfile();
    if (t === 'admin') loadAdmin();
    if (t === 'help') loadHelp();
}

// Promo modal
function openPromo() {
    haptic('medium');
    document.getElementById('pm').classList.add('on');
    document.getElementById('pi').focus();
}

function closePromo() {
    document.getElementById('pm').classList.remove('on');
    document.getElementById('pi').value = '';
}

async function applyPromo() {
    const c = document.getElementById('pi').value.trim();
    if (!c) { toast('Введите промокод', 'error'); return; }
    haptic('medium');
    try {
        const d = await apiPost('/promo/apply', { code: c });
        if (d.ok) {
            haptic('success');
            toast(d.result.message);
            closePromo();
            if (cur === 'profile') loadProfile();
            if (cur === 'home') loadHome();
        } else {
            haptic('error');
            toast(d.error || 'Неверный промокод', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

// Share
function shareRef(code) {
    haptic('medium');
    const text = '🔐 VPN\n\nРеферальный код: ' + code + '\nПрисоединяйся!';
    if (tg.showPopup) {
        tg.showPopup({ title: 'Поделиться', message: 'Отправьте друзьям', buttons: [{ id: 'copy', type: 'default', text: 'Копировать' }] }, (b) => { if (b === 'copy') copyText(text, 'Скопировано'); });
    } else {
        copyText(text, 'Скопировано');
    }
}

function openExternal(url) {
    if (tg.openLink) { tg.openLink({ url: url }); }
    else { window.open(url, '_blank'); }
}

// ── Loaders / Renderers ──

function setLoading(id) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '<div class="ld"><div class="ls"></div></div></div>';
}

function setError(id, msg, retry) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '<div class="em"><div class="ei">😕</div><div class="em-title">' + escapeHtml(msg) + '</div>' + (retry ? '<button class="btn bp" onclick="' + retry + '">Повторить</button>' : '') + '</div>';
}

async function loadHome() {
    const el = document.getElementById('home-c');
    const c = cacheGet('home', 30000);
    if (c) { renderHome(c); return; }
    setLoading('home-c');
    try {
        const d = await api('/profile');
        if (d.ok) { cacheSet('home', d); renderHome(d); }
        else { setError('home-c', d.error || 'Ошибка загрузки', 'loadHome()'); }
    } catch (e) { setError('home-c', e.message, 'loadHome()'); }
}

function renderHome(d) {
    const u = d.user || {};
    const keys = d.active_keys || [];
    const total = (d.active_keys || []).length + (d.archive_keys || []).length;
    const hasKey = keys.length > 0;
    let h = '<div class="hero">';
    h += '<div style="display:flex;align-items:center;gap:14px;margin-bottom:12px">';
    h += '<div class="av">' + (u.full_name ? escapeHtml(u.full_name[0].toUpperCase()) : '👤') + '</div>';
    h += '<div><div style="font-size:13px;opacity:.85">👋 Добро пожаловать</div>';
    h += '<div style="font-size:20px;font-weight:800">' + escapeHtml(u.full_name || 'Пользователь') + '</div>';
    h += '</div></div>';
    if (hasKey) {
        const first = keys[0];
        h += '<div style="font-size:13px;opacity:.9;margin-bottom:12px">Активна <b>' + escapeHtml(first.name) + '</b> • ' + fmtDays(daysLeft(first.expires_at)) + ' осталось</div>';
    }
    h += '<div class="stats-grid">';
    h += '<div class="stat-item"><div class="stat-val">' + total + '</div><div class="stat-lbl">Подписок</div></div>';
    h += '<div class="stat-item"><div class="stat-val">' + fmtMoney(u.balance || 0) + '</div><div class="stat-lbl">Баланс</div></div>';
    if (u.referrals_count) {
        h += '<div class="stat-item"><div class="stat-val">' + u.referrals_count + '</div><div class="stat-lbl">Рефералов</div></div>';
    }
    h += '<div class="stat-item"><div class="stat-val">' + (keys.length) + '</div><div class="stat-lbl">Активных</div></div>';
    h += '</div>';
    if (hasKey) {
        h += '<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">�� Активные подписки</div>';
        for (const k of keys) {
            const dl = daysLeft(k.expires_at);
            const pct = Math.min(100, Math.max(5, dl));
            h += '<div style="margin-bottom:12px;padding-bottom:10px;border-bottom:1px solid var(--br)">';
            h += '<div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px"><span>' + escapeHtml(k.name) + '</span><span style="color:var(--a);font-weight:700">' + fmtDays(dl) + '</span></div>';
            h += '<div class="db"><div class="df" style="width:' + pct + '%"></div></div>';
            h += '<div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap">';
            h += '<button class="btn bs" onclick="copyText(\'' + (k.access_url || '').replace(/'/g, "\\'") + '\',\'Ключ скопирован\')">📋 Копировать ключ</button>';
            h += '<button class="btn bs" style="background:var(--a3);color:var(--a)" onclick="extendKeyDialog(' + k.id + ')">⏳ Продлить</button>';
            h += '</div>';
            h += '</div>';
        }
        h += '</div>';
    }
    h += '<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">⚡ Быстрые действия</div>';
    h += '<button class="pb" onclick="go(\'buy\')"><span class="pb-icon" style="background:rgba(0,212,170,.15);font-size:18px">💳</span><div><div style="font-size:14px;font-weight:700">Купить подписку</div><div style="font-size:12px;color:var(--hi)">Выберите подходящий план</div></div></button>';
    h += '<button class="pb" onclick="openPromo()"><span class="pb-icon" style="background:rgba(245,158,11,.15);font-size:18px">🎁</span><div><div style="font-size:14px;font-weight:700">Активировать промокод</div><div style="font-size:12px;color:var(--hi)">Получите бонусы</div></div></button>';
    if (u.referral_code) {
        h += '<button class="pb" onclick="shareRef(\'' + u.referral_code.replace(/'/g, "\\'") + '\')"><span class="pb-icon" style="background:rgba(14,165,233,.15);font-size:18px">🔗</span><div><div style="font-size:14px;font-weight:700">Пригласить друга</div><div style="font-size:12px;color:var(--hi)">Реферальная программа</div></div></button>';
    }
    h += '</div>';
    document.getElementById('home-c').innerHTML = h;
}

async function loadSubs() {
    setLoading('subs-c');
    try {
        const d = await api('/profile');
        if (d.ok) { renderSubs(d); }
        else { setError('subs-c', d.error || 'Ошибка загрузки', 'loadSubs()'); }
    } catch (e) { setError('subs-c', e.message, 'loadSubs()'); }
}

function renderSubs(d) {
    const keys = (d.active_keys || []).concat(d.archive_keys || []);
    if (!keys.length) {
        document.getElementById('subs-c').innerHTML = '<div class="em"><div class="ei">🔑</div><div class="em-title">Нет подписок</div><button class="btn bp" onclick="go(\'buy\')">Купить подписку</button></div>';
        return;
    }
    let h = '';
    for (const k of keys) {
        const isActive = k.status === 'active';
        h += '<div class="kc ' + (isActive ? 'active-key' : '') + '">';
        h += '<div class="kn">' + escapeHtml(k.name) + '</div>';
        h += '<div class="ke">' + (isActive ? '✅ Активна до ' + fmtDate(k.expires_at) : '❌ Истекла') + '</div>';
        if (isActive && k.access_url) {
            h += '<div class="ku" onclick="copyText(\'' + k.access_url.replace(/'/g, "\\'") + '\',\'Ключ скопирован\')">' + escapeHtml(k.access_url) + '</div>';
        }
        if (isActive) {
            h += '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">';
            h += '<button class="btn bs" onclick="copyText(\'' + (k.access_url || '').replace(/'/g, "\\'") + '\',\'Ключ скопирован\')">📋 Копировать</button>';
            h += '<button class="btn bs" style="background:var(--a3);color:var(--a)" onclick="extendKeyDialog(' + k.id + ')">⏳ Продлить</button>';
            h += '</div>';
        }
        h += '</div>';
    }
    document.getElementById('subs-c').innerHTML = h;
}

async function loadBuy() {
    setLoading('buy-c');
    try {
        const [plansRes, settingsRes] = await Promise.all([api('/plans'), api('/settings')]);
        if (plansRes.ok && settingsRes.ok) {
            plans = plansRes.plans || [];
            HAS_YK = settingsRes.has_yookassa;
            HAS_SBP = settingsRes.has_sbp;
            HAS_CB = settingsRes.has_cryptobot;
            HAS_FK = settingsRes.has_freekassa;
            HAS_STARS = settingsRes.has_stars;
            STARS = settingsRes.stars_rate || 1.5;
            BOT_UNAME = settingsRes.bot_username || '';
            renderBuy(plansRes.plans || []);
        } else {
            setError('buy-c', (plansRes.error || settingsRes.error || 'Ошибка загрузки'), 'loadBuy()');
        }
    } catch (e) { setError('buy-c', e.message, 'loadBuy()'); }
}

function renderBuy(plansList) {
    if (!plansList.length) {
        document.getElementById('buy-c').innerHTML = '<div class="em"><div class="ei">📭</div><div class="em-title">Нет доступных планов</div>';
        return;
    }
    let h = '<div class="sl2">Выберите тариф</div>';
    for (const p of plansList) {
        h += '<div class="pc" onclick="selPlan=' + p.id + ';renderBuyPlans()">';
        h += '<div class="pn">' + escapeHtml(p.name) + '</div>';
        h += '<div class="pp">' + fmtMoney(p.price) + '</div>';
        h += '<div class="pd">' + p.duration_days + ' ' + (p.duration_days === 1 ? 'день' : p.duration_days < 5 ? 'дня' : 'дней') + '</div>';
        if (p.description) h += '<div class="pd">' + escapeHtml(p.description) + '</div>';
        h += '</div>';
    }
    document.getElementById('buy-c').innerHTML = h;
}

function renderBuyPlans() {
    const p = plans.find(x => x.id === selPlan);
    if (!p) { go('buy'); return; }
    let h = '<button class="btn bo" style="margin-bottom:12px" onclick="renderBuy(plans)">← Назад к тарифам</button>';
    h += '<div class="pc sel" style="margin-bottom:16px">';
    h += '<div class="pn">' + escapeHtml(p.name) + '</div>';
    h += '<div class="pp">' + fmtMoney(p.price) + '</div>';
    h += '<div class="pd">' + p.duration_days + ' ' + (p.duration_days === 1 ? 'день' : p.duration_days < 5 ? 'дня' : 'дней') + '</div>';
    if (p.description) h += '<div class="pd">' + escapeHtml(p.description) + '</div>';
    h += '</div>';
    h += '<div class="sl2">Способ оплаты</div>';
    h += '<button class="pb" onclick="payBalance(' + p.id + ')"><span class="pb-icon" style="background:rgba(0,212,170,.15);font-size:18px">💰</span><div><div style="font-size:14px;font-weight:700">Баланс</div><div style="font-size:12px;color:var(--hi)">Оплатить с баланса</div></div></button>';
    if (HAS_YK) {
        h += '<button class="pb" onclick="payYookassa(' + p.id + ')"><span class="pb-icon" style="background:rgba(245,158,11,.15);font-size:18px">💳</span><div><div style="font-size:14px;font-weight:700">Банковская карта</div><div style="font-size:12px;color:var(--hi)">ЮKassa</div></div></button>';
    }
    if (HAS_SBP) {
        h += '<button class="pb" onclick="paySBP(' + p.id + ')"><span class="pb-icon" style="background:rgba(34,197,94,.15);font-size:18px">🏦</span><div><div style="font-size:14px;font-weight:700">СБП</div><div style="font-size:12px;color:var(--hi)">Система быстрых платежей</div></div></button>';
    }
    if (HAS_CB) {
        h += '<button class="pb" onclick="payCrypto(' + p.id + ')"><span class="pb-icon" style="background:rgba(139,92,246,.15);font-size:18px">₿</span><div><div style="font-size:14px;font-weight:700">Криптовалюта</div><div style="font-size:12px;color:var(--hi)">CryptoBot</div></div></button>';
    }
    if (HAS_FK) {
        h += '<button class="pb" onclick="payFreeKassa(' + p.id + ')"><span class="pb-icon" style="background:rgba(236,72,153,.15);font-size:18px">💎</span><div><div style="font-size:14px;font-weight:700">FreeKassa</div><div style="font-size:12px;color:var(--hi)">Разные способы оплаты</div></div></button>';
    }
    if (HAS_STARS) {
        const stars = Math.ceil(p.price * STARS);
        h += '<button class="pb" onclick="payStars(' + p.id + ')"><span class="pb-icon" style="background:rgba(234,179,8,.15);font-size:18px">⭐</span><div><div style="font-size:14px;font-weight:700">Telegram Stars</div><div style="font-size:12px;color:var(--hi)">' + stars + ' ⭐</div></div></button>';
    }
    document.getElementById('buy-c').innerHTML = h;
}

// ── Payment handlers ─────────────────────────────────────────

async function payBalance(planId) {
    haptic('medium');
    try {
        const d = await apiPost('/pay/balance', { plan_id: planId });
        if (d.ok) {
            haptic('success');
            toast('✅ Подписка активирована!');
            go('subs');
        } else {
            haptic('error');
            toast(d.error || 'Ошибка оплаты', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

async function payYookassa(planId) {
    haptic('medium');
    try {
        const d = await apiPost('/pay/yookassa', { plan_id: planId });
        if (d.ok && d.payment_url) {
            openExternal(d.payment_url);
            if (d.payment_id) setTimeout(() => checkPayment(d.payment_id, 'yookassa'), 5000);
        } else {
            haptic('error');
            toast(d.error || 'Ошибка создания платежа', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

async function paySBP(planId) {
    haptic('medium');
    try {
        const d = await apiPost('/pay/sbp', { plan_id: planId });
        if (d.ok && d.payment_url) {
            openExternal(d.payment_url);
            if (d.payment_id) setTimeout(() => checkPayment(d.payment_id, 'yookassa'), 5000);
        } else {
            haptic('error');
            toast(d.error || 'Ошибка создания платежа', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

async function payCrypto(planId) {
    haptic('medium');
    try {
        const d = await apiPost('/pay/cryptobot', { plan_id: planId });
        if (d.ok && d.payment_url) {
            openExternal(d.payment_url);
            if (d.payment_id) setTimeout(() => checkPayment(d.payment_id, 'cryptobot'), 5000);
        } else {
            haptic('error');
            toast(d.error || 'Ошибка создания платежа', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

async function payFreeKassa(planId) {
    haptic('medium');
    try {
        const d = await apiPost('/pay/freekassa', { plan_id: planId });
        if (d.ok && d.payment_url) {
            openExternal(d.payment_url);
            if (d.payment_id) setTimeout(() => checkPayment(d.payment_id, 'freekassa'), 5000);
        } else {
            haptic('error');
            toast(d.error || 'Ошибка создания платежа', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

async function payStars(planId) {
    haptic('medium');
    try {
        const d = await apiPost('/pay/stars', { plan_id: planId });
        if (d.ok && d.payment_url) {
            openExternal(d.payment_url);
        } else {
            haptic('error');
            toast(d.error || 'Ошибка создания платежа', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

async function checkPayment(paymentId, provider) {
    try {
        const d = await api('/pay/check/' + paymentId);
        if (d.ok && d.status === 'succeeded') {
            haptic('success');
            toast('✅ Платёж подтверждён!');
            go('subs');
            return;
        }
        if (d.ok && d.status === 'pending') {
            setTimeout(() => checkPayment(paymentId, provider), 5000);
            return;
        }
        if (d.status === 'failed') {
            haptic('error');
            toast('❌ Платёж отменён', 'error');
        }
    } catch (e) {
        console.error('Payment check error:', e);
    }
}

// ── Extend key ───────────────────────────────────────────────

function extendKeyDialog(keyId) {
    haptic('medium');
    const p = plans[0];
    if (!p) { toast('Нет доступных тарифов', 'error'); return; }
    const days = p.duration_days || 30;
    const price = p.price || 0;
    if (confirm('Продлить подписку на ' + days + ' дней за ' + fmtMoney(price) + '?')) {
        extendKey(keyId, p.id);
    }
}

async function extendKey(keyId, planId) {
    haptic('medium');
    try {
        const d = await apiPost('/extend/key', { key_id: keyId, plan_id: planId });
        if (d.ok) {
            haptic('success');
            toast('✅ Подписка продлена!');
            if (cur === 'subs') loadSubs();
            if (cur === 'home') loadHome();
        } else {
            haptic('error');
            toast(d.error || 'Ошибка продления', 'error');
        }
    } catch (e) {
        haptic('error');
        toast('Ошибка: ' + e.message, 'error');
    }
}

// ── Profile ──────────────────────────────────────────────────

async function loadProfile() {
    setLoading('profile-c');
    try {
        const [profileRes, statsRes] = await Promise.all([api('/profile'), api('/user/stats')]);
        if (profileRes.ok) {
            renderProfile(profileRes, statsRes.ok ? statsRes.stats : null);
        } else {
            setError('profile-c', profileRes.error || 'Ошибка загрузки', 'loadProfile()');
        }
    } catch (e) { setError('profile-c', e.message, 'loadProfile()'); }
}

function renderProfile(d, stats) {
    const u = d.user || {};
    let h = '<div class="card">';
    h += '<div style="display:flex;align-items:center;gap:14px;margin-bottom:12px">';
    h += '<div class="av" style="width:64px;height:64px;font-size:28px">' + (u.full_name ? escapeHtml(u.full_name[0].toUpperCase()) : '👤') + '</div>';
    h += '<div><div style="font-size:18px;font-weight:800">' + escapeHtml(u.full_name || 'Пользователь') + '</div>';
    h += '<div style="font-size:13px;color:var(--hi)">ID: ' + (u.id || '—') + '</div></div>';
    h += '</div>';
    h += '<div class="div"></div>';
    h += '<div class="sr"><div class="sr-label">Баланс</div><div class="sr-val">' + fmtMoney(u.balance || 0) + '</div></div>';
    if (stats) {
        h += '<div class="sr"><div class="sr-label">Всего потрачено</div><div class="sr-val">' + fmtMoney(stats.total_spent || 0) + '</div></div>';
        h += '<div class="sr"><div class="sr-label">Активных ключей</div><div class="sr-val">' + (stats.active_keys || 0) + '</div></div>';
        h += '<div class="sr"><div class="sr-label">Рефералов</div><div class="sr-val">' + (stats.referrals || 0) + '</div></div>';
    }
    h += '</div>';
    if (u.referral_code) {
        h += '<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">🔗 Реферальная программа</div>';
        h += '<div class="rb"><div class="rl">' + escapeHtml(u.referral_code) + '</div></div>';
        h += '<button class="btn bp" style="margin-top:10px" onclick="shareRef(\'' + u.referral_code.replace(/'/g, "\\'") + '\')">Поделиться кодом</button>';
        h += '</div>';
    }
    h += '<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">⚙️ Настройки</div>';
    h += '<button class="pb" onclick="toggleTheme()"><span class="pb-icon" style="background:rgba(100,116,139,.15);font-size:18px">🎨</span><div><div style="font-size:14px;font-weight:700">Тема</div><div style="font-size:12px;color:var(--hi)">Светлая / тёмная</div></div></button>';
    h += '</div>';
    document.getElementById('profile-c').innerHTML = h;
}

// ── Admin ────────────────────────────────────────────────────

async function loadAdmin() {
    setLoading('admin-c');
    try {
        const d = await api('/admin/stats');
        if (d.ok) { renderAdmin(d); }
        else { setError('admin-c', d.error || 'Ошибка загрузки', 'loadAdmin()'); }
    } catch (e) { setError('admin-c', e.message, 'loadAdmin()'); }
}

function renderAdmin(d) {
    const s = d.stats || {};
    let h = '<div class="hero" style="background:linear-gradient(135deg,#c084fc,#ef4444)"><div style="font-size:22px;font-weight:800">👑 Админ панель</div></div>';
    h += '<div class="stats-grid">';
    h += '<div class="stat-item"><div class="stat-val">' + (s.total_users || 0) + '</div><div class="stat-lbl">Пользователей</div></div>';
    h += '<div class="stat-item"><div class="stat-val">' + (s.active_keys || 0) + '</div><div class="stat-lbl">Активных ключей</div></div>';
    h += '<div class="stat-item"><div class="stat-val">' + (s.total_payments || 0) + '</div><div class="stat-lbl">Платежей</div></div>';
    h += '<div class="stat-item"><div class="stat-val">' + fmtMoney(s.total_revenue || 0) + '</div><div class="stat-lbl">Выручка</div></div>';
    h += '</div>';
    h += '<div class="card"><div style="font-size:15px;font-weight:700;margin-bottom:10px">🖥 Статус серверов</div><div id="admin-srv"></div></div>';
    document.getElementById('admin-c').innerHTML = h;
    loadServerStatus('admin-srv');
}

// ── Help ─────────────────────────────────────────────────────

async function loadHelp() {
    setLoading('help-c');
    try {
        const d = await api('/faq');
        if (d.ok) { renderHelp(d); }
        else { setError('help-c', d.error || 'Ошибка загрузки', 'loadHelp()'); }
    } catch (e) { setError('help-c', e.message, 'loadHelp()'); }
}

function renderHelp(d) {
    const items = d.faq || [];
    if (!items.length) {
        document.getElementById('help-c').innerHTML = '<div class="em"><div class="ei">❓</div><div class="em-title">Нет вопросов</div></div>';
        return;
    }
    let h = '<div class="sl2">Частые вопросы</div>';
    for (const item of items) {
        h += '<div class="faq-item" onclick="this.classList.toggle(\'open\')">';
        h += '<div class="faq-q">' + escapeHtml(item.question) + '<span class="faq-icon">▼</span></div>';
        h += '<div class="faq-a">' + escapeHtml(item.answer) + '</div>';
        h += '</div>';
    }
    document.getElementById('help-c').innerHTML = h;
}

// ── Server status ────────────────────────────────────────────

async function loadServerStatus(containerId) {
    containerId = containerId || 'srv-status';
    const el = document.getElementById(containerId);
    if (!el) return;
    try {
        const d = await api('/servers/status');
        if (!d.ok) return;
        let h = '';
        for (const s of d.servers || []) {
            const dotClass = s.status === 'online' ? '' : s.status === 'degraded' ? 'warn' : 'off';
            h += '<div class="srv-item"><div class="srv-dot ' + dotClass + '"></div><div style="flex:1"><div style="font-size:13px;font-weight:700">' + escapeHtml(s.name) + ' ' + escapeHtml(s.region) + '</div><div style="font-size:11px;color:var(--hi)">Пинг: ' + (s.ping || 0) + ' мс · Загрузка: ' + (s.load || 0) + '%</div></div><span class="chip chip-' + (s.status === 'online' ? 'ok' : s.status === 'degraded' ? 'warn' : 'danger') + '">' + (s.status === 'online' ? 'Работает' : s.status === 'degraded' ? 'Деградация' : 'Недоступен') + '</span></div>';
        }
        if (containerId === 'srv-status') {
            el.style.display = 'block';
            el.innerHTML = '<div class="card" style="margin-bottom:12px"><div style="font-size:15px;font-weight:700;margin-bottom:10px">🖥 Статус серверов</div>' + h + '</div>';
        } else {
            el.innerHTML = h;
        }
    } catch (e) { console.error('Server status error:', e); }
}

// ── Init ─────────────────────────────────────────────────────

async function init() {
    tg.ready();
    tg.expand();

    const initData = tg.initData || '';
    let authMethod = 'initData';
    if (!initData && tg.initDataUnsafe && tg.initDataUnsafe.user) {
        authMethod = 'fallback';
    }

    try {
        console.log('[MiniApp] Loading settings...');
        const sd = await api('/settings', {}, 3);
        if (sd.ok) {
            HAS_YK = sd.has_yookassa || false;
            HAS_SBP = sd.has_sbp || false;
            HAS_CB = sd.has_cryptobot || false;
            HAS_FK = sd.has_freekassa || false;
            HAS_STARS = sd.has_stars || false;
            STARS = sd.stars_rate || 1.5;
            BOT_UNAME = sd.bot_username || '';
        }
        console.log('[MiniApp] Settings loaded');

        let d;
        if (authMethod === 'initData') {
            console.log('[MiniApp] Auth via initData');
            d = await apiPost('/auth', { initData: initData });
        } else {
            console.log('[MiniApp] Auth via fallback (initDataUnsafe)');
            d = await apiPost('/auth-fallback', { user: tg.initDataUnsafe.user });
        }

        if (!d.ok) {
            console.error('[MiniApp] Auth failed:', d.error, d.detail);
            document.getElementById('home-c').innerHTML = '<div class="em"><div class="ei" style="font-size:64px">🔐</div><div class="em-title">Ошибка авторизации</div><div class="em-sub">' + (d.error || 'Неизвестная ошибка') + '</div><div style="font-size:12px;color:var(--hi);margin-top:8px">Попробуйте перезапустить приложение</div></div>';
            return;
        }

        console.log('[MiniApp] Auth success, user:', d.user?.id);
        U = d.user || null;
        if (U && U.is_admin) { document.getElementById('n-admin').style.display = 'block'; }

        loadHome();
        loadServerStatus();

        setInterval(() => {
            if (cur === 'home') loadHome();
            if (cur === 'profile') loadProfile();
            if (cur === 'admin') loadAdmin();
        }, 30000);
    } catch (e) {
        console.error('[MiniApp] Init error:', e);
        document.getElementById('home-c').innerHTML = '<div class="em"><div class="ei" style="font-size:64px">😕</div><div class="em-title">Ошибка подключения</div><div class="em-sub">' + e.message + '</div><button class="btn bp" onclick="init()">Повторить</button></div>';
    }
}

// Pull to refresh
let ptrStart = 0, ptrPulling = false;
document.addEventListener('touchstart', e => { if (window.scrollY <= 0) { ptrStart = e.touches[0].clientY; ptrPulling = true; } }, { passive: true });
document.addEventListener('touchmove', e => { if (!ptrPulling) return; const dy = e.touches[0].clientY - ptrStart; if (dy > 80 && window.scrollY <= 0) { e.preventDefault(); const el = document.getElementById('ptr'); if (el) el.classList.add('show'); } }, { passive: false });
document.addEventListener('touchend', () => { if (!ptrPulling) return; ptrPulling = false; const el = document.getElementById('ptr'); if (el) el.classList.remove('show'); if (cur === 'home') loadHome(); if (cur === 'subs') loadSubs(); if (cur === 'buy') loadBuy(); if (cur === 'profile') loadProfile(); if (cur === 'help') loadHelp(); if (cur === 'admin') loadAdmin(); toast('🔄 Обновлено', 'info'); }, { passive: true });

// Start
document.addEventListener('DOMContentLoaded', init);
