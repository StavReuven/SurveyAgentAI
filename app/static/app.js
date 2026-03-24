const state = {
  selectedCampaignId: null,
  questions: [],
  execution: null,
  policy: null,
};

const toast = document.getElementById("toast");
const campaignCards = document.getElementById("campaign-cards");
const builderPanel = document.getElementById("builder-panel");
const builderTitle = document.getElementById("builder-title");
const questionList = document.getElementById("question-list");
const ruleList = document.getElementById("rule-list");
const participantList = document.getElementById("participant-list");
const ruleSource = document.getElementById("rule-source");
const ruleTarget = document.getElementById("rule-target");
const ruleAction = document.getElementById("rule-action");
const executionStatus = document.getElementById("execution-status");
const policyForm = document.getElementById("policy-form");
const attemptList = document.getElementById("attempt-list");
const refreshAttemptsButton = document.getElementById("refresh-attempts");

const jsonHeaders = { "Content-Type": "application/json" };

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 1800);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = "הבקשה נכשלה";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (err) {
      // Keep default error message when no JSON payload is returned.
    }
    throw new Error(detail);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : {};
}

function questionNameMap() {
  return Object.fromEntries(state.questions.map((q) => [q.id, q.key]));
}

function renderQuestions() {
  questionList.innerHTML = "";
  state.questions.forEach((q, idx) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="line">
        <strong>${idx + 1}. ${q.key}</strong>
        <span>${q.question_type}</span>
      </div>
      <div>${q.prompt}</div>
      <div class="actions">
        <button data-up="${q.id}" class="alt">למעלה</button>
        <button data-down="${q.id}" class="alt">למטה</button>
        <button data-delete="${q.id}" class="warn">מחיקה</button>
      </div>
    `;
    questionList.appendChild(li);
  });
  fillRuleQuestionSelects();
}

function renderRules(items) {
  const map = questionNameMap();
  ruleList.innerHTML = "";
  items.forEach((rule) => {
    const li = document.createElement("li");
    const source = map[rule.source_question_id] || `Q${rule.source_question_id}`;
    const target = rule.target_question_id ? map[rule.target_question_id] || `Q${rule.target_question_id}` : "-";
    li.innerHTML = `
      <div><strong>#${rule.id}</strong> אם <code>${source}</code> ${rule.operator} <code>${rule.value}</code></div>
      <div>פעולה: <strong>${rule.action}</strong> יעד: <strong>${target}</strong> עדיפות: ${rule.priority}</div>
      <div class="actions">
        <button data-rule-delete="${rule.id}" class="warn">מחיקה</button>
      </div>
    `;
    ruleList.appendChild(li);
  });
}

function renderParticipants(items) {
  participantList.innerHTML = "";
  items.forEach((p) => {
    const li = document.createElement("li");
    li.innerHTML = `<div><strong>${p.phone_number}</strong> (${p.locale || "לא זמין"})</div><div>${p.full_name || "ללא שם"} | ${p.status}</div>`;
    participantList.appendChild(li);
  });
}

function fillRuleQuestionSelects() {
  const options = state.questions
    .map((q) => `<option value="${q.id}">${q.key}</option>`)
    .join("");
  ruleSource.innerHTML = options;
  ruleTarget.innerHTML = `<option value="">(ללא)</option>${options}`;
}

function renderExecutionStatus() {
  if (!state.execution) {
    executionStatus.textContent = "סטטוס ביצוע: לא ידוע";
    return;
  }
  const lastTick = state.execution.last_tick_at
    ? ` | עדכון אחרון: ${new Date(state.execution.last_tick_at).toLocaleString()}`
    : "";
  executionStatus.textContent = `סטטוס ביצוע: ${state.execution.state}${lastTick}`;
}

function renderPolicyForm() {
  if (!state.policy) {
    return;
  }
  policyForm.window_start_hour.value = state.policy.window_start_hour;
  policyForm.window_end_hour.value = state.policy.window_end_hour;
  policyForm.max_attempts.value = state.policy.max_attempts;
  policyForm.retry_delay_minutes.value = state.policy.retry_delay_minutes;
  policyForm.cooldown_hours.value = state.policy.cooldown_hours;
  policyForm.max_calls_per_minute.value = state.policy.max_calls_per_minute;
  policyForm.enabled.checked = state.policy.enabled;
}

function renderAttempts(items) {
  attemptList.innerHTML = "";
  if (!items.length) {
    attemptList.innerHTML = "<li>עדיין אין ניסיונות חיוג.</li>";
    return;
  }

  items.forEach((item) => {
    const li = document.createElement("li");
    const when = new Date(item.started_at).toLocaleString();
    li.innerHTML = `
      <div class="line">
        <strong>${item.outcome.toUpperCase()}</strong>
        <span>#${item.attempt_number} | ${when}</span>
      </div>
      <div>${item.participant_phone}</div>
      <div>${item.note || "-"}</div>
    `;
    attemptList.appendChild(li);
  });
}

async function loadAttempts(campaignId) {
  const attempts = await api(`/api/campaigns/${campaignId}/attempts?limit=30`);
  renderAttempts(attempts);
}

async function loadCampaignCards() {
  const campaigns = await api("/api/campaigns/summary");
  campaignCards.innerHTML = "";
  if (!campaigns.length) {
    campaignCards.innerHTML = "<p>עדיין אין קמפיינים. אפשר ליצור את הראשון למעלה.</p>";
    return;
  }

  campaigns.forEach((c) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${c.name}</h3>
      <div class="meta">${c.language} | ${c.timezone} | סטטוס: <strong>${c.status}</strong></div>
      <div class="meta">שאלות: ${c.question_count} | משתתפים: ${c.participant_count}</div>
      <div class="actions">
        <button data-open="${c.id}">פתיחת הבונה</button>
        <button data-duplicate="${c.id}" class="alt">שכפול</button>
        <button data-start="${c.id}" class="alt">התחלה</button>
        <button data-pause="${c.id}" class="alt">השהיה</button>
        <button data-resume="${c.id}" class="alt">חידוש</button>
        <button data-stop="${c.id}" class="warn">עצירה</button>
        <button data-delete-campaign="${c.id}" class="warn">מחיקה</button>
      </div>
    `;
    campaignCards.appendChild(card);
  });
}

async function openCampaign(id) {
  state.selectedCampaignId = id;
  const campaign = await api(`/api/campaigns/${id}`);
  builderTitle.textContent = `בונה קמפיינים: ${campaign.name}`;
  builderPanel.classList.remove("hidden");

  state.questions = await api(`/api/campaigns/${id}/questions`);
  renderQuestions();

  const rules = await api(`/api/campaigns/${id}/rules`);
  renderRules(rules);

  const participants = await api(`/api/campaigns/${id}/participants`);
  renderParticipants(participants);

  state.execution = await api(`/api/campaigns/${id}/execution`);
  renderExecutionStatus();

  state.policy = await api(`/api/campaigns/${id}/policy`);
  renderPolicyForm();

  await loadAttempts(id);
}

function getSelectedCampaignId() {
  if (!state.selectedCampaignId) {
    throw new Error("יש לפתוח קמפיין קודם");
  }
  return state.selectedCampaignId;
}

document.getElementById("campaign-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const payload = Object.fromEntries(form.entries());

  try {
    await api("/api/campaigns", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    });
    e.target.reset();
    showToast("הקמפיין נוצר");
    await loadCampaignCards();
  } catch (err) {
    showToast(err.message);
  }
});

campaignCards.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) {
    return;
  }

  const id =
    btn.dataset.open ||
    btn.dataset.duplicate ||
    btn.dataset.start ||
    btn.dataset.pause ||
    btn.dataset.resume ||
    btn.dataset.stop ||
    btn.dataset.deleteCampaign;
  if (!id) {
    return;
  }

  try {
    if (btn.dataset.open) {
      await openCampaign(Number(id));
      showToast("הקמפיין נפתח");
    } else if (btn.dataset.duplicate) {
      await api(`/api/campaigns/${id}/duplicate`, { method: "POST" });
      showToast("הקמפיין שוכפל");
      await loadCampaignCards();
    } else if (btn.dataset.start) {
      await api(`/api/campaigns/${id}/start`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("הקמפיין הופעל");
      await loadCampaignCards();
    } else if (btn.dataset.pause) {
      await api(`/api/campaigns/${id}/pause`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("הקמפיין הושהה");
      await loadCampaignCards();
    } else if (btn.dataset.resume) {
      await api(`/api/campaigns/${id}/resume`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("הקמפיין חודש");
      await loadCampaignCards();
    } else if (btn.dataset.stop) {
      await api(`/api/campaigns/${id}/stop`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("הקמפיין נעצר");
      await loadCampaignCards();
    } else if (btn.dataset.deleteCampaign) {
      await api(`/api/campaigns/${id}`, { method: "DELETE" });
      if (state.selectedCampaignId === Number(id)) {
        builderPanel.classList.add("hidden");
        state.selectedCampaignId = null;
      }
      showToast("הקמפיין נמחק");
      await loadCampaignCards();
    }
  } catch (err) {
    showToast(err.message);
  }
});

policyForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const campaignId = getSelectedCampaignId();
    const form = new FormData(e.target);
    const payload = {
      window_start_hour: Number(form.get("window_start_hour")),
      window_end_hour: Number(form.get("window_end_hour")),
      max_attempts: Number(form.get("max_attempts")),
      retry_delay_minutes: Number(form.get("retry_delay_minutes")),
      cooldown_hours: Number(form.get("cooldown_hours")),
      max_calls_per_minute: Number(form.get("max_calls_per_minute")),
      enabled: form.get("enabled") === "on",
    };

    state.policy = await api(`/api/campaigns/${campaignId}/policy`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    });
    renderPolicyForm();
    showToast("מדיניות החיוג נשמרה");
  } catch (err) {
    showToast(err.message);
  }
});

refreshAttemptsButton.addEventListener("click", async () => {
  try {
    const campaignId = getSelectedCampaignId();
    await loadAttempts(campaignId);
    showToast("ניסיונות רועננו");
  } catch (err) {
    showToast(err.message);
  }
});

document.getElementById("question-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const campaignId = getSelectedCampaignId();
    const form = new FormData(e.target);
    const options = (form.get("options") || "")
      .toString()
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

    const payload = {
      key: form.get("key"),
      prompt: form.get("prompt"),
      question_type: form.get("question_type"),
      required: form.get("required") === "on",
      config: {},
    };
    if (payload.question_type === "mcq") {
      payload.config.options = options;
    }

    await api(`/api/campaigns/${campaignId}/questions`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    });

    e.target.reset();
    state.questions = await api(`/api/campaigns/${campaignId}/questions`);
    renderQuestions();
    await loadCampaignCards();
    showToast("השאלה נוספה");
  } catch (err) {
    showToast(err.message);
  }
});

questionList.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) {
    return;
  }

  try {
    const campaignId = getSelectedCampaignId();
    if (btn.dataset.delete) {
      await api(`/api/questions/${btn.dataset.delete}`, { method: "DELETE" });
    }

    if (btn.dataset.up || btn.dataset.down) {
      const currentId = Number(btn.dataset.up || btn.dataset.down);
      const idx = state.questions.findIndex((q) => q.id === currentId);
      const newIndex = btn.dataset.up ? idx - 1 : idx + 1;
      if (newIndex < 0 || newIndex >= state.questions.length) {
        return;
      }
      const copy = [...state.questions];
      [copy[idx], copy[newIndex]] = [copy[newIndex], copy[idx]];
      await api(`/api/campaigns/${campaignId}/questions/reorder`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ question_ids: copy.map((q) => q.id) }),
      });
    }

    state.questions = await api(`/api/campaigns/${campaignId}/questions`);
    renderQuestions();
    await loadCampaignCards();
    showToast("השאלות עודכנו");
  } catch (err) {
    showToast(err.message);
  }
});

document.getElementById("rule-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const campaignId = getSelectedCampaignId();
    const form = new FormData(e.target);
    const action = form.get("action");

    const payload = {
      source_question_id: Number(form.get("source_question_id")),
      operator: form.get("operator"),
      value: form.get("value"),
      action,
      target_question_id: form.get("target_question_id") ? Number(form.get("target_question_id")) : null,
      priority: Number(form.get("priority")),
    };

    if (action !== "goto") {
      payload.target_question_id = null;
    }

    await api(`/api/campaigns/${campaignId}/rules`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(payload),
    });

    const rules = await api(`/api/campaigns/${campaignId}/rules`);
    renderRules(rules);
    showToast("הכלל נוסף");
  } catch (err) {
    showToast(err.message);
  }
});

ruleAction.addEventListener("change", () => {
  ruleTarget.disabled = ruleAction.value !== "goto";
});

ruleList.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn || !btn.dataset.ruleDelete) {
    return;
  }

  try {
    const campaignId = getSelectedCampaignId();
    await api(`/api/rules/${btn.dataset.ruleDelete}`, { method: "DELETE" });
    const rules = await api(`/api/campaigns/${campaignId}/rules`);
    renderRules(rules);
    showToast("הכלל נמחק");
  } catch (err) {
    showToast(err.message);
  }
});

document.getElementById("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const campaignId = getSelectedCampaignId();
    const form = new FormData(e.target);
    await api(`/api/campaigns/${campaignId}/participants/upload`, {
      method: "POST",
      body: form,
    });

    const participants = await api(`/api/campaigns/${campaignId}/participants`);
    renderParticipants(participants);
    await loadCampaignCards();
    showToast("המשתתפים הועלו");
  } catch (err) {
    showToast(err.message);
  }
});

loadCampaignCards().catch((err) => {
  showToast(err.message);
});
