/* Settings page logic: tabs, general (localStorage) prefs, API keys, DNC, users, audit. */

const $ = (id) => document.getElementById(id);

async function api(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(body.detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

// ── Tabs ─────────────────────────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll('.settings-tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.settings-tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.settings-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      $(`panel-${btn.dataset.tab}`).classList.add('active');
    });
  });
}

// ── General prefs (client-side only; no backend endpoint yet) ─────────────
const GENERAL_FIELDS = {
  'g-mirroring-enabled': 'checked', 'g-tone': 'value', 'g-pace': 'value', 'g-adapt-seconds': 'value',
  'g-energy': 'value', 'g-hybrid-enabled': 'checked', 'g-rapport': 'value', 'g-max-wait': 'value',
  'g-retries': 'value', 'g-bias-enabled': 'checked', 'g-demo': 'value', 'g-anomaly': 'value',
  'g-behavior': 'value', 'g-opt-in': 'checked', 'g-recording': 'checked', 'g-anon': 'checked',
  'n-intervention': 'checked', 'n-completed': 'checked', 'n-anomaly': 'checked', 'n-daily-report': 'checked',
};
const GENERAL_STORAGE_KEY = 'voicesurvey.settings.general';

function wireRangeLabels() {
  [['g-tone', 'g-tone-val'], ['g-pace', 'g-pace-val'], ['g-energy', 'g-energy-val'],
   ['g-rapport', 'g-rapport-val'], ['g-demo', 'g-demo-val'], ['g-anomaly', 'g-anomaly-val'],
   ['g-behavior', 'g-behavior-val']].forEach(([inputId, labelId]) => {
    const input = $(inputId);
    input.addEventListener('input', () => { $(labelId).textContent = input.value; });
  });
}

function loadGeneralPrefs() {
  const saved = JSON.parse(localStorage.getItem(GENERAL_STORAGE_KEY) || '{}');
  Object.entries(GENERAL_FIELDS).forEach(([id, prop]) => {
    if (saved[id] === undefined) return;
    const el = $(id);
    el[prop] = saved[id];
    if (prop === 'value') el.dispatchEvent(new Event('input'));
  });
}

function saveGeneralPrefs() {
  const out = {};
  Object.entries(GENERAL_FIELDS).forEach(([id, prop]) => { out[id] = $(id)[prop]; });
  localStorage.setItem(GENERAL_STORAGE_KEY, JSON.stringify(out));
  $('g-save-status').textContent = 'השינויים נשמרו ✓';
  setTimeout(() => { $('g-save-status').textContent = ''; }, 2500);
}

function resetGeneralPrefs() {
  localStorage.removeItem(GENERAL_STORAGE_KEY);
  location.reload();
}

// ── API Keys / Connection Status (SAA-131) ─────────────────────────────────
const PROVIDER_LABELS = { anthropic: 'LLM (Anthropic)', twilio: 'טלפוניה (Twilio)', stt: 'Speech-to-Text', tts: 'Text-to-Speech' };
const KEY_LABELS = {
  api_key: 'API Key', account_sid: 'Account SID', auth_token: 'Auth Token', phone_number: 'מספר טלפון',
};

async function loadProviders() {
  const container = $('providers-list');
  try {
    const providers = await api('/api/settings/providers');
    container.innerHTML = providers.map(renderProviderCard).join('');
    providers.forEach((p) => wireProviderCard(p));
  } catch (e) {
    container.innerHTML = `<div style="color:var(--danger);">שגיאה בטעינת ספקים: ${e.message}</div>`;
  }
}

function renderProviderCard(p) {
  const keysHtml = Object.entries(p.keys).map(([keyName, info]) => `
    <div style="margin-bottom:10px;">
      <div class="setting-row-desc" style="margin-bottom:4px;">${KEY_LABELS[keyName] || keyName}${info.configured ? ` — ${info.masked_value}` : ''}</div>
      <input class="form-input" type="password" placeholder="${info.configured ? 'הזן ערך חדש להחלפה' : 'לא הוגדר'}" data-provider="${p.provider}" data-key="${keyName}">
    </div>`).join('');

  return `
    <div class="setting-row" style="display:block; padding: 16px 0;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <div class="setting-row-label">${PROVIDER_LABELS[p.provider] || p.provider}</div>
        <span class="pill ${p.configured ? 'pill-ok' : 'pill-warn'}" id="status-${p.provider}">${p.configured ? 'מחובר' : 'לא מוגדר'}</span>
      </div>
      ${keysHtml}
      <div style="display:flex; gap:8px;">
        <button class="btn-primary" data-save="${p.provider}">שמור</button>
        <button class="btn-secondary" data-check="${p.provider}">בדוק חיבור</button>
      </div>
    </div>`;
}

function wireProviderCard(p) {
  const saveBtn = document.querySelector(`[data-save="${p.provider}"]`);
  const checkBtn = document.querySelector(`[data-check="${p.provider}"]`);

  saveBtn.addEventListener('click', async () => {
    const inputs = document.querySelectorAll(`input[data-provider="${p.provider}"]`);
    const values = {};
    inputs.forEach((input) => { if (input.value) values[input.dataset.key] = input.value; });
    if (!Object.keys(values).length) return;
    try {
      await api(`/api/settings/providers/${p.provider}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ values }),
      });
      await loadProviders();
    } catch (e) {
      alert(`שגיאה בשמירה: ${e.message}`);
    }
  });

  checkBtn.addEventListener('click', async () => {
    try {
      const result = await api(`/api/settings/providers/${p.provider}/health-check`, { method: 'POST' });
      const badge = $(`status-${p.provider}`);
      badge.textContent = result.status === 'configured' ? 'מחובר' : 'לא מוגדר';
      badge.className = `pill ${result.status === 'configured' ? 'pill-ok' : 'pill-warn'}`;
    } catch (e) {
      alert(`שגיאה בבדיקה: ${e.message}`);
    }
  });
}

// ── Consent / Do-Not-Call (SAA-140) ────────────────────────────────────────
async function loadDnc() {
  const rows = $('dnc-rows');
  try {
    const entries = await api('/api/settings/dnc');
    rows.innerHTML = entries.length
      ? entries.map((e) => `
        <tr>
          <td>${e.phone_number}</td>
          <td>${e.reason || '—'}</td>
          <td>${e.added_by || '—'}</td>
          <td>${new Date(e.created_at).toLocaleString('he-IL')}</td>
          <td><button class="btn-danger" data-remove-dnc="${e.id}">הסר</button></td>
        </tr>`).join('')
      : '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">הרשימה ריקה</td></tr>';

    rows.querySelectorAll('[data-remove-dnc]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await api(`/api/settings/dnc/${btn.dataset.removeDnc}`, { method: 'DELETE' });
        loadDnc();
      });
    });
  } catch (e) {
    rows.innerHTML = `<tr><td colspan="5" style="color:var(--danger);">שגיאה: ${e.message}</td></tr>`;
  }
}

function wireDncForm() {
  $('dnc-add-btn').addEventListener('click', async () => {
    const errorBox = $('dnc-error');
    errorBox.style.display = 'none';
    const phone = $('dnc-phone').value.trim();
    if (!phone) return;
    try {
      await api('/api/settings/dnc', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone_number: phone, reason: $('dnc-reason').value.trim() || null, added_by: window.currentUser?.email }),
      });
      $('dnc-phone').value = '';
      $('dnc-reason').value = '';
      loadDnc();
    } catch (e) {
      errorBox.textContent = e.message;
      errorBox.style.display = 'block';
    }
  });
}

// ── Users & Roles (SAA-136, admin only) ────────────────────────────────────
async function loadUsers() {
  const rows = $('user-rows');
  try {
    const users = await api('/api/auth/users');
    rows.innerHTML = users.map((u) => `
      <tr>
        <td>${u.email}</td>
        <td><span class="pill pill-role-${u.role}">${u.role}</span></td>
        <td>${u.is_active ? '<span class="pill pill-ok">פעיל</span>' : '<span class="pill pill-warn">מושבת</span>'}</td>
        <td>${u.is_active ? `<button class="btn-danger" data-deactivate="${u.id}">השבת</button>` : ''}</td>
      </tr>`).join('');

    rows.querySelectorAll('[data-deactivate]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await api(`/api/auth/users/${btn.dataset.deactivate}`, { method: 'DELETE' });
        loadUsers();
      });
    });
  } catch (e) {
    rows.innerHTML = `<tr><td colspan="4" style="color:var(--danger);">שגיאה: ${e.message}</td></tr>`;
  }
}

function wireUserForm() {
  $('user-add-btn').addEventListener('click', async () => {
    const errorBox = $('user-error');
    errorBox.style.display = 'none';
    const email = $('user-email').value.trim();
    const password = $('user-password').value;
    const role = $('user-role').value;
    if (!email || !password) return;
    try {
      await api('/api/auth/users', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, role }),
      });
      $('user-email').value = '';
      $('user-password').value = '';
      loadUsers();
    } catch (e) {
      errorBox.textContent = e.message;
      errorBox.style.display = 'block';
    }
  });
}

// ── Audit log (SAA-143, admin only) ─────────────────────────────────────────
async function loadAudit() {
  const rows = $('audit-rows');
  try {
    const entries = await api('/api/settings/audit');
    rows.innerHTML = entries.length
      ? entries.map((e) => `
        <tr>
          <td>${e.category}</td>
          <td>${e.action}</td>
          <td>${e.actor || '—'}</td>
          <td>${e.detail || '—'}</td>
          <td>${new Date(e.created_at).toLocaleString('he-IL')}</td>
        </tr>`).join('')
      : '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">אין רשומות</td></tr>';
  } catch (e) {
    rows.innerHTML = `<tr><td colspan="5" style="color:var(--danger);">שגיאה: ${e.message}</td></tr>`;
  }
}

// ── Init ────────────────────────────────────────────────────────────────
(async function init() {
  const user = await guardPage(); // any authenticated role may view Settings
  if (!user) return;

  initTabs();
  wireRangeLabels();
  loadGeneralPrefs();
  $('g-save').addEventListener('click', saveGeneralPrefs);
  $('g-reset').addEventListener('click', resetGeneralPrefs);

  loadProviders();
  loadDnc();
  wireDncForm();

  if (user.role === 'admin') {
    document.querySelectorAll('.admin-only').forEach((el) => { el.style.display = ''; });
    loadUsers();
    wireUserForm();
    loadAudit();
  }
})();
