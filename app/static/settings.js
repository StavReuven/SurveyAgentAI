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

// ── General prefs — privacy + notification toggles only. Voice Mirroring,
// Hybrid Intervention and Data Quality live on the server now (see the
// loadMirroringGeneral/loadEscalationSettings/loadQualitySettings blocks
// below) — they used to be silently localStorage-only with no effect on
// real calls; that's no longer true for these three cards. ────────────────
const GENERAL_FIELDS = {
  'g-opt-in': 'checked', 'g-recording': 'checked', 'g-anon': 'checked',
  'n-intervention': 'checked', 'n-completed': 'checked', 'n-anomaly': 'checked', 'n-daily-report': 'checked',
};
const GENERAL_STORAGE_KEY = 'voicesurvey.settings.general';

function wireRangeLabels() {
  [['g-rate', 'g-rate-val'], ['g-pitch', 'g-pitch-val'], ['g-killswitch', 'g-killswitch-val'],
   ['g-alpha', 'g-alpha-val'], ['g-rapport', 'g-rapport-val'], ['g-anomaly', 'g-anomaly-val'],
  ].forEach(([inputId, labelId]) => {
    const input = $(inputId);
    input.addEventListener('input', () => { $(labelId).textContent = input.value; });
  });
}

// ── Voice Mirroring — same live global settings as voice.html's panel ──────
async function loadMirroringGeneral() {
  try {
    const s = await api('/api/mirroring/settings');
    $('g-mirroring-enabled').checked = s.enabled;
    $('g-rate').value = Math.round(s.max_rate_delta * 100);
    $('g-rate-val').textContent = Math.round(s.max_rate_delta * 100);
    $('g-pitch').value = s.max_pitch_semitones;
    $('g-pitch-val').textContent = s.max_pitch_semitones;
    $('g-killswitch').value = Math.round(s.kill_switch_rapport_threshold * 100);
    $('g-killswitch-val').textContent = Math.round(s.kill_switch_rapport_threshold * 100);
    $('g-alpha').value = Math.round(s.smoothing_alpha * 100);
    $('g-alpha-val').textContent = Math.round(s.smoothing_alpha * 100);
  } catch (e) {
    $('g-mirroring-status').textContent = `שגיאה בטעינה: ${e.message}`;
  }
}

let _mirroringSaveTimer = null;
function saveMirroringGeneral() {
  clearTimeout(_mirroringSaveTimer);
  _mirroringSaveTimer = setTimeout(async () => {
    try {
      await api('/api/mirroring/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: $('g-mirroring-enabled').checked,
          max_rate_delta: parseInt($('g-rate').value, 10) / 100,
          max_pitch_semitones: parseFloat($('g-pitch').value),
          kill_switch_rapport_threshold: parseInt($('g-killswitch').value, 10) / 100,
          smoothing_alpha: parseInt($('g-alpha').value, 10) / 100,
          calibration_turns: 1,
        }),
      });
      $('g-mirroring-status').textContent = 'השינויים נשמרו ✓';
      setTimeout(() => { $('g-mirroring-status').textContent = ''; }, 2000);
    } catch (e) {
      $('g-mirroring-status').textContent = `שגיאה בשמירה: ${e.message}`;
    }
  }, 400);
}

function wireMirroringGeneral() {
  ['g-mirroring-enabled', 'g-rate', 'g-pitch', 'g-killswitch', 'g-alpha'].forEach((id) => {
    $(id).addEventListener('change', saveMirroringGeneral);
  });
}

// ── Hybrid Intervention System — real EscalationConfig, live in the pipeline ──
async function loadEscalationSettings() {
  try {
    const s = await api('/api/escalation/settings');
    $('g-hybrid-enabled').checked = s.enabled;
    $('g-rapport').value = Math.round(s.low_rapport_threshold * 100);
    $('g-rapport-val').textContent = Math.round(s.low_rapport_threshold * 100);
    $('g-retries').value = s.max_retries;
  } catch (e) {
    $('g-hybrid-status').textContent = `שגיאה בטעינה: ${e.message}`;
  }
}

let _escalationSaveTimer = null;
function saveEscalationSettings() {
  clearTimeout(_escalationSaveTimer);
  _escalationSaveTimer = setTimeout(async () => {
    try {
      await api('/api/escalation/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: $('g-hybrid-enabled').checked,
          low_rapport_threshold: parseInt($('g-rapport').value, 10) / 100,
          max_retries: parseInt($('g-retries').value, 10) || 1,
        }),
      });
      $('g-hybrid-status').textContent = 'השינויים נשמרו ✓';
      setTimeout(() => { $('g-hybrid-status').textContent = ''; }, 2000);
    } catch (e) {
      $('g-hybrid-status').textContent = `שגיאה בשמירה: ${e.message}`;
    }
  }, 400);
}

function wireEscalationSettings() {
  ['g-hybrid-enabled', 'g-rapport', 'g-retries'].forEach((id) => {
    $(id).addEventListener('change', saveEscalationSettings);
  });
}

// ── Data Quality / Anomaly Detection — real thresholds in analytics/router.py ──
async function loadQualitySettings() {
  try {
    const s = await api('/api/quality/settings');
    $('g-anomaly-enabled').checked = s.enabled;
    $('g-anomaly').value = s.anomaly_quality_threshold;
    $('g-anomaly-val').textContent = s.anomaly_quality_threshold;
  } catch (e) {
    $('g-quality-status').textContent = `שגיאה בטעינה: ${e.message}`;
  }
}

let _qualitySaveTimer = null;
function saveQualitySettings() {
  clearTimeout(_qualitySaveTimer);
  _qualitySaveTimer = setTimeout(async () => {
    try {
      await api('/api/quality/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          enabled: $('g-anomaly-enabled').checked,
          anomaly_quality_threshold: parseFloat($('g-anomaly').value),
        }),
      });
      $('g-quality-status').textContent = 'השינויים נשמרו ✓';
      setTimeout(() => { $('g-quality-status').textContent = ''; }, 2000);
    } catch (e) {
      $('g-quality-status').textContent = `שגיאה בשמירה: ${e.message}`;
    }
  }, 400);
}

function wireQualitySettings() {
  ['g-anomaly-enabled', 'g-anomaly'].forEach((id) => {
    $(id).addEventListener('change', saveQualitySettings);
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

  wireMirroringGeneral();
  wireEscalationSettings();
  wireQualitySettings();
  loadMirroringGeneral();
  loadEscalationSettings();
  loadQualitySettings();

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
