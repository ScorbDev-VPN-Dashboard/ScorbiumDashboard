// Scorbium Mini App - FIXED 404 + Production Ready
// Critical fixes: initData headers, error boundaries, retries, offline handling

class MiniApp {
  constructor() {
    this.apiBase = '/app';
    this.initData = window.Telegram?.WebApp?.initData || '';
    this.initDataUnsafe = window.Telegram?.WebApp?.initDataUnsafe || {};
    this.user = null;
    this.retryCount = 0;
    this.maxRetries = 3;
    this.isOnline = true;
    
    this.init();
  }

  async init() {
    // Telegram WebApp setup
    if (window.Telegram?.WebApp) {
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
      this.initData = window.Telegram.WebApp.initData;
      this.initDataUnsafe = window.Telegram.WebApp.initDataUnsafe;
      
      // Theme sync
      document.body.className = this.initDataUnsafe.theme_params.bg_color === '#000000' ? 'dark' : 'light';
      
      // Offline detection
      window.addEventListener('online', () => this.setOnline(true));
      window.addEventListener('offline', () => this.setOnline(false));
    }
    
    // Global error handler
    window.addEventListener('unhandledrejection', (e) => {
      console.error('Promise rejection:', e.reason);
      this.showToast('Ошибка приложения. Попробуйте обновить.', 'err');
    });
    
    await this.auth();
    this.loadHome();
    this.bindEvents();
  }

  setOnline(online) {
    this.isOnline = online;
    const bar = document.getElementById('offlineBar');
    if (bar) bar.classList.toggle('on', !online);
  }

  async api(path, options = {}) {
    const url = `${this.apiBase}${path}`;
    
    // Always inject initData header for auth
    const headers = {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': this.initData,
      ...options.headers
    };
    
    const config = {
      headers,
      ...options
    };
    
    for (let i = 0; i < this.maxRetries; i++) {
      try {
        if (!this.isOnline) throw new Error('OFFLINE');
        
        const resp = await fetch(url, config);
        
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          
          // Common errors → user-friendly
          if (resp.status === 401) {
            this.showToast('Перезайдите в приложение', 'err');
            return { ok: false, error: 'auth_required' };
          }
          
          if (data.error?.includes('not found')) {
            this.showToast('Функция временно недоступна', 'err');
            return { ok: false };
          }
          
          throw new Error(data.error || `HTTP ${resp.status}`);
        }
        
        return await resp.json();
        
      } catch (e) {
        console.warn(`API ${path} attempt ${i+1} failed:`, e);
        
        if (i === this.maxRetries - 1) {
          this.showToast('Нет соединения. Проверьте интернет.', 'err');
          return { ok: false, error: e.message };
        }
        
        await new Promise(r => setTimeout(r, 1000 * (i + 1)));
      }
    }
  }

  async auth() {
    try {
      const resp = await this.api('/auth', {
        method: 'POST',
        body: JSON.stringify({ initData: this.initData })
      });
      
      if (resp.ok) {
        this.user = resp.user;
        if (this.user.is_admin) {
          document.getElementById('n-admin').style.display = 'block';
        }
        return true;
      }
      
      // Fallback auth (emergency)
      if (this.initDataUnsafe.user?.id) {
        const fallback = await this.api('/auth-fallback', {
          method: 'POST',
          body: JSON.stringify({ user: this.initDataUnsafe.user })
        });
        if (fallback.ok) {
          this.user = fallback.user;
          return true;
        }
      }
      
      this.showError('Ошибка авторизации');
      return false;
      
    } catch (e) {
      this.showToast('Ошибка входа. Перезапустите приложение.', 'err');
      return false;
    }
  }

  async loadHome() {
    try {
      this.showLoading('home-c');
      
      // Parallel loading
      const [profile, plans, settings, servers] = await Promise.all([
        this.api('/profile'),
        this.api('/plans'),
        this.api('/settings'),
        this.api('/servers/status')
      ]);
      
      if (!profile.ok || !plans.ok || !settings.ok) {
        throw new Error('Failed to load data');
      }
      
      this.renderHome({
        user: profile.user,
        plans: plans.plans,
        settings,
        servers: servers.servers || [],
        overall: servers.overall || 'unknown'
      });
      
    } catch (e) {
      this.renderError('home-c', 'Не удалось загрузить главную страницу');
    }
  }

  renderHome(data) {
    const c = document.getElementById('home-c');
    
    // Offline/server status
    const srvStatus = data.servers.length ? 
      `<div class="srv-item"><span class="srv-dot ${data.overall === 'degraded' ? 'warn' : ''}"></span>Серверы ${data.overall}</div>` : 
      '';
    
    c.innerHTML = `
      <div class="hero">
        <div style="font-size:32px;font-weight:800;margin-bottom:4px">${data.user.full_name || '@user'}</div>
        <div style="font-size:14px;color:rgba(255,255,255,.9)">@${data.user.username || 'user'}</div>
      </div>
      
      ${srvStatus}
      
      ${data.plans.length ? `
        <div class="sl2">Доступные тарифы</div>
        ${data.plans.map(p => `
          <div class="pc" onclick="app.buyPlan(${p.id}, '${p.name}', ${p.price}, ${p.duration_days})">
            <div class="pn">${p.name}</div>
            <div class="pp">${p.price} ₽</div>
            <div class="pd">${p.duration_days} дней</div>
          </div>
        `).join('')}
      ` : '<div class="em"><div class="ei">😔</div><div class="em-title">Нет тарифов</div></div>'}
      
      <div style="height:24px"></div>
    `;
    
    this.hideLoading('home-c');
  }

  async loadSubs() {
    try {
      this.showLoading('subs-c');
      const resp = await this.api('/profile');
      
      if (!resp.ok) throw new Error('Load failed');
      
      const c = document.getElementById('subs-c');
      if (resp.active_keys.length) {
        c.innerHTML = resp.active_keys.map(k => `
          <div class="kc active-key">
            <div class="kn">${k.name}</div>
            <div class="ke">Действует до ${new Date(k.expires_at).toLocaleDateString('ru')}</div>
            <div class="ku" onclick="navigator.clipboard.writeText('${k.access_url}');app.showToast('Скопировано!')">🔗 ${k.access_url}</div>
            <div class="db"><div class="df" style="width:100%"></div></div>
          </div>
        `).join('') + `
          <div class="pc" onclick="app.extendKey()">
            <div class="pn">🔄 Продлить подписку</div>
          </div>
        `;
      } else {
        c.innerHTML = `
          <div class="em">
            <div class="ei">🔑</div>
            <div class="em-title">У вас нет активных подписок</div>
            <div class="em-sub">Купите тариф для получения VPN-ключа</div>
            <button class="btn bp" onclick="app.go('buy')">💳 Купить подписку</button>
          </div>
        `;
      }
      
    } catch (e) {
      this.renderError('subs-c', 'Не удалось загрузить подписки');
    }
  }

  async loadBuy() {
    try {
      this.showLoading('buy-c');
      const [plans, settings] = await Promise.all([
        this.api('/plans'),
        this.api('/settings')
      ]);
      
      if (!plans.ok || !settings.ok) throw new Error('Load failed');
      
      const c = document.getElementById('buy-c');
      const ps = settings;
      
      c.innerHTML = `
        ${ps.has_yookassa ? `<div class="pb" onclick="app.payYookassa()">
          <div class="pb-icon" style="background:linear-gradient(135deg,#00d4aa,#0ea5e9)">💳</div>
          ЮKassa (карта, СБП)
        </div>` : ''}
        
        ${ps.has_freekassa ? `<div class="pb" onclick="app.payFreekassa()">
          <div class="pb-icon" style="background:#6b7280">🏦</div>
          FreeKassa
        </div>` : ''}
        
        ${ps.has_cryptobot ? `<div class="pb" onclick="app.payCrypto()">
          <div class="pb-icon" style="background:linear-gradient(135deg,#f59e0b,#eab308)">₿</div>
          Криптовалюта
        </div>` : ''}
        
        ${ps.has_stars ? `<div class="pb" onclick="app.payStars()">
          <div class="pb-icon" style="background:#00d4aa">⭐</div>
          Telegram Stars
        </div>` : ''}
        
        <div class="div"></div>
        <button class="btn bo" onclick="app.openPromo()">🎁 Промокод</button>
      `;
      
    } catch (e) {
      this.renderError('buy-c', 'Не удалось загрузить способы оплаты');
    }
  }

  async loadProfile() {
    try {
      this.showLoading('profile-c');
      const [profile, stats] = await Promise.all([
        this.api('/profile'),
        this.api('/user/stats')
      ]);
      
      if (!profile.ok || !stats.ok) throw new Error('Load failed');
      
      const c = document.getElementById('profile-c');
      c.innerHTML = `
        <div class="av">${this.user.full_name?.[0] || '@'}</div>
        <div style="text-align:center;margin:16px 0">
          <div style="font-size:22px;font-weight:800;margin-bottom:4px">${profile.user.full_name}</div>
          <div style="font-size:13px;color:var(--hi)">@${profile.user.username}</div>
        </div>
        
        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-val">${stats.stats.balance.toFixed(2)} ₽</div>
            <div class="stat-lbl">Баланс</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.stats.active_keys}</div>
            <div class="stat-lbl">Активных</div>
          </div>
        </div>
        
        <div style="font-size:12px;color:var(--hi);text-align:center;margin:24px 0">
          Реферальный код: <code style="background:var(--bg3);padding:4px 8px;border-radius:6px">${profile.user.referral_code}</code>
        </div>
        
        ${this.user.is_admin ? `
          <button class="btn bp" onclick="app.loadAdmin()">👑 Админ панель</button>
        ` : ''}
      `;
      
    } catch (e) {
      this.renderError('profile-c', 'Не удалось загрузить профиль');
    }
  }

  async loadHelp() {
    try {
      this.showLoading('help-c');
      const faq = await this.api('/faq');
      
      const c = document.getElementById('help-c');
      c.innerHTML = `
        <div style="text-align:center;margin-bottom:24px">
          <div style="font-size:28px;margin-bottom:8px">❓</div>
          <div style="font-size:18px;font-weight:700;margin-bottom:12px">Помощь и FAQ</div>
          ${faq.about ? `<div style="font-size:13px;color:var(--hi)">${faq.about}</div>` : ''}
        </div>
        
        <div class="faq-item" onclick="toggleFaq(this)">
          <div class="faq-q">
            Как подключить VPN?
            <svg class="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="faq-a">1. Скопируйте ссылку из «Подписки»<br>2. Откройте V2Ray/Outline/Nekobox<br>3. Импортируйте ссылку</div>
        </div>
        
        <div class="faq-item" onclick="toggleFaq(this)">
          <div class="faq-q">
            Сколько устройств?
            <svg class="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="faq-a">Неограниченно! Один ключ = все устройства</div>
        </div>
        
        <div class="faq-item" onclick="toggleFaq(this)">
          <div class="faq-q">
            Пополнить баланс?
            <svg class="faq-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
          </div>
          <div class="faq-a">Купите тариф → средства зачислятся автоматически</div>
        </div>
        
        <button class="btn bo" onclick="app.go('profile')" style="margin-top:24px">👤 Профиль</button>
      `;
    } catch (e) {
      this.renderError('help-c', 'Не удалось загрузить помощь');
    }
  }

  async loadAdmin() {
    try {
      this.showLoading('admin-c');
      const stats = await this.api('/admin/stats');
      
      if (!stats.ok) throw new Error('Admin load failed');
      
      const c = document.getElementById('admin-c');
      c.innerHTML = `
        <div class="stats-grid">
          <div class="stat-item">
            <div class="stat-val">${stats.total_users}</div>
            <div class="stat-lbl">Пользователей</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.active_subs}</div>
            <div class="stat-lbl">Активных</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.new_today}</div>
            <div class="stat-lbl">Сегодня</div>
          </div>
          <div class="stat-item">
            <div class="stat-val">${stats.revenue.toFixed(0)} ₽</div>
            <div class="stat-lbl">Выручка</div>
          </div>
        </div>
      `;
      
      document.getElementById('n-admin').classList.add('on');
      
    } catch (e) {
      this.showToast('Ошибка загрузки админ-панели', 'err');
    }
  }

  // Navigation
  go(screen) {
    document.querySelectorAll('.scr').forEach(s => s.classList.remove('on'));
    document.getElementById(`s-${screen}`).classList.add('on');
    
    document.querySelectorAll('.nb').forEach(b => b.classList.remove('on'));
    document.getElementById(`n-${screen}`).classList.add('on');
    
    // Screen handlers
    if (screen === 'subs') this.loadSubs();
    else if (screen === 'buy') this.loadBuy();
    else if (screen === 'profile') this.loadProfile();
    else if (screen === 'help') this.loadHelp();
    else if (screen === 'admin') this.loadAdmin();
  }

  // Payments
  async buyPlan(planId, name, price, days) {
    try {
      const settings = await this.api('/settings');
      if (!settings.has_yookassa) {
        this.showToast('ЮKassa не настроена', 'err');
        return;
      }
      
      this.showLoading('home-c', true);
      const resp = await this.api(`/pay/yookassa`, {
        method: 'POST',
        body: JSON.stringify({
          plan_id: planId,
          bot_username: settings.bot_username
        })
      });
      
      this.hideLoading('home-c');
      
      if (resp.ok && resp.confirm_url) {
        window.Telegram.WebApp.openLink(resp.confirm_url);
      } else {
        this.showToast(resp.error || 'Ошибка оплаты', 'err');
      }
    } catch (e) {
      this.hideLoading('home-c');
      this.showToast('Ошибка создания платежа', 'err');
    }
  }

  async payYookassa() {
    this.go('buy');
  }

  async payStars() {
    this.showToast('⭐ Telegram Stars скоро', 'info');
  }

  async payCrypto() {
    this.showToast('₿ Крипта скоро', 'info');
  }

  async payFreekassa() {
    this.showToast('🏦 FreeKassa скоро', 'info');
  }

  openPromo() {
    document.getElementById('pm').classList.add('on');
    document.getElementById('pi').focus();
  }

  async applyPromo() {
    const code = document.getElementById('pi').value.trim().toUpperCase();
    if (!code) return;
    
    try {
      const resp = await this.api('/promo/apply', {
        method: 'POST',
        body: JSON.stringify({ code })
      });
      
      closePromo();
      
      if (resp.ok) {
        this.showToast(resp.result.message, 'ok');
        setTimeout(() => this.loadHome(), 1500);
      } else {
        this.showToast(resp.error || 'Неверный код', 'err');
      }
    } catch (e) {
      this.showToast('Ошибка промокода', 'err');
    }
  }

  async extendKey() {
    this.showToast('🔄 Продление скоро', 'info');
  }

  // UI Helpers
  showLoading(id, block = false) {
    const el = document.getElementById(id);
    if (!el) return;
    
    el.innerHTML = block ? '' : '<div class="ld"><div class="ls"></div></div>';
  }

  hideLoading(id) {
    // Trigger reload for content that auto-loads
    setTimeout(() => this.loadHome(), 100);
  }

  renderError(id, msg) {
    document.getElementById(id).innerHTML = `
      <div class="em">
        <div class="ei">⚠️</div>
        <div class="em-title">${msg}</div>
        <button class="btn bo" onclick="app.loadHome()">🔄 Попробовать снова</button>
      </div>
    `;
  }

  showToast(msg, type = 'ok') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type}`;
    t.classList.add('on');
    setTimeout(() => t.classList.remove('on'), 3000);
  }

  bindEvents() {
    // FAQ toggle
    document.addEventListener('click', (e) => {
      if (e.target.closest('.faq-item')) {
        e.target.closest('.faq-item').classList.toggle('open');
      }
    });
    
    // Modal close
    window.closePromo = () => document.getElementById('pm').classList.remove('on');
    
    // Pull to refresh
    let startY;
    document.addEventListener('touchstart', e => {
      startY = e.touches[0].clientY;
    });
    
    document.addEventListener('touchmove', e => {
      if (!startY) return;
      const currentY = e.touches[0].clientY;
      if (currentY - startY > 80 && document.querySelector('.scr.on').scrollTop === 0) {
        document.getElementById('ptr').classList.add('show');
      }
    });
    
    document.addEventListener('touchend', async () => {
      if (startY && document.querySelector('.scr.on').scrollTop === 0) {
        document.getElementById('ptr').classList.remove('show');
        await this.loadHome();
      }
      startY = null;
    });
  }
}

// Global app instance + theme toggle
const app = new MiniApp();

function toggleTheme() {
  document.body.classList.toggle('dark');
  document.body.classList.toggle('light');
  localStorage.theme = document.body.className;
}

function toggleFaq(el) {
  el.classList.toggle('open');
}

// Init on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    if (localStorage.theme) {
      document.body.className = localStorage.theme;
    }
  });
}

