const state = {
  selectedCampaignId: null,
  questions: [],
  execution: null,
  policy: null,
  isDirty: false,
};

const toast = document.getElementById("toast");
const closeConfirmModal = document.getElementById("close-confirm-modal");
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

function resetDirty() { state.isDirty = false; }

function closeBuilder() {
  builderPanel.classList.add("hidden");
  // Move builder back outside the grid so it doesn't take up space
  if (builderPanel.parentElement === campaignCards) {
    campaignCards.parentElement.appendChild(builderPanel);
  }
  state.selectedCampaignId = null;
  resetDirty();
}

function showCloseModal() {
  closeConfirmModal.classList.remove("hidden");
}

function hideCloseModal() {
  closeConfirmModal.classList.add("hidden");
}

document.getElementById("close-builder-btn").addEventListener("click", () => {
  if (state.isDirty) {
    showCloseModal();
  } else {
    closeBuilder();
  }
});

document.getElementById("modal-cancel-btn").addEventListener("click", hideCloseModal);

document.getElementById("modal-discard-btn").addEventListener("click", () => {
  hideCloseModal();
  closeBuilder();
});

document.getElementById("modal-save-btn").addEventListener("click", async () => {
  hideCloseModal();
  // Submit the policy form to save its values, then close
  policyForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  // Wait briefly for the async save to complete before closing
  setTimeout(closeBuilder, 400);
});

// Track unsaved changes in builder panel via event delegation
builderPanel.addEventListener("input", () => { state.isDirty = true; });
builderPanel.addEventListener("change", () => { state.isDirty = true; });

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = "Request failed";
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
        <button data-up="${q.id}" class="alt">Up</button>
        <button data-down="${q.id}" class="alt">Down</button>
        <button data-delete="${q.id}" class="warn">Delete</button>
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
      <div><strong>#${rule.id}</strong> if <code>${source}</code> ${rule.operator} <code>${rule.value}</code></div>
      <div>action: <strong>${rule.action}</strong> target: <strong>${target}</strong> priority: ${rule.priority}</div>
      <div class="actions">
        <button data-rule-delete="${rule.id}" class="warn">Delete</button>
      </div>
    `;
    ruleList.appendChild(li);
  });
}

function renderParticipants(items) {
  participantList.innerHTML = "";
  items.forEach((p) => {
    const li = document.createElement("li");
    li.innerHTML = `<div><strong>${p.phone_number}</strong> (${p.locale || "n/a"})</div><div>${p.full_name || "Unnamed"} | ${p.status}</div>`;
    participantList.appendChild(li);
  });
}

function fillRuleQuestionSelects() {
  const options = state.questions
    .map((q) => `<option value="${q.id}">${q.key}</option>`)
    .join("");
  ruleSource.innerHTML = options;
  ruleTarget.innerHTML = `<option value="">(none)</option>${options}`;
}

function renderExecutionStatus() {
  if (!state.execution) {
    executionStatus.textContent = "Execution: unknown";
    return;
  }
  const lastTick = state.execution.last_tick_at
    ? ` | last tick: ${new Date(state.execution.last_tick_at).toLocaleString()}`
    : "";
  executionStatus.textContent = `Execution: ${state.execution.state}${lastTick}`;
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
    attemptList.innerHTML = "<li>No call attempts yet.</li>";
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

  // Rescue builder from grid before wiping innerHTML
  if (builderPanel.parentElement === campaignCards) {
    campaignCards.parentElement.appendChild(builderPanel);
  }

  campaignCards.innerHTML = "";
  if (!campaigns.length) {
    campaignCards.innerHTML = "<p>אין סקרים פעילים. צור סקר חדש למעלה.</p>";
    return;
  }

  campaigns.forEach((c) => {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.campaignId = c.id;
    card.style.display = "flex";
    card.style.flexDirection = "column";

    let statusColor = "background:#f1f5f9; color:#475569;";
    let statusText = "טיוטה";
    if(c.status === "active") { statusColor = "background:#dcfce7; color:#166534;"; statusText="פעיל"; }
    if(c.status === "paused") { statusColor = "background:#fef3c7; color:#b45309;"; statusText="מושהה"; }

    card.innerHTML = `
      <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-weight:bold; font-size:16px;">${c.name}</span>
        <div style="display: flex; gap: 8px;">
          <span class="status-badge" style="${statusColor}; padding:4px 8px; border-radius:12px; font-size:11px;">${statusText}</span>
        </div>
      </div>
      
      <div class="grid-2 camp-stats" style="display:grid; grid-template-columns:1fr 1fr; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); margin: 16px 0; padding: 12px 0;">
        <div style="text-align:center;">
          <div class="camp-stats-num" style="font-size: 24px; font-weight: bold; color: var(--primary);">${c.question_count}</div>
          <div class="camp-stats-label" style="font-size: 12px; color: var(--text-muted);">שאלות</div>
        </div>
        <div style="text-align:center;">
          <div class="camp-stats-num" style="font-size: 24px; font-weight: bold; color: var(--primary);">${c.participant_count}</div>
          <div class="camp-stats-label" style="font-size: 12px; color: var(--text-muted);">משתתפים</div>
        </div>
      </div>

      <div class="actions" style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; justify-content: center;">
        <button data-open="${c.id}" class="btn-primary" style="font-size: 12px; padding: 6px 12px;">עריכת סקר</button>
        <button data-duplicate="${c.id}" style="background: white; border: 1px solid var(--border); color: #475569; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;">שכפל</button>
        <button data-start="${c.id}" style="background: white; border: 1px solid #16a34a; color: #16a34a; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;">הפעל</button>
        <button data-pause="${c.id}" style="background: white; border: 1px solid #d97706; color: #d97706; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;">השהה</button>
        <button data-resume="${c.id}" style="background: white; border: 1px solid #0284c7; color: #0284c7; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;">המשך</button>
        <button data-stop="${c.id}" style="background: #fee2e2; border: 1px solid #dc2626; color: #dc2626; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;">עצור</button>
        <button data-delete-campaign="${c.id}" style="background: white; border: 1px solid #dc2626; color: #dc2626; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;">מחק</button>
      </div>

      <div class="progress-container" style="margin-top: auto;">
        <div class="progress-labels" style="display:flex; justify-content:space-between; font-size:12px; color:var(--text-muted);">
          <span>התקדמות</span>
        </div>
        <div class="progress-bar-bg" style="height: 6px; background: var(--border); border-radius: 3px; width: 100%; margin: 8px 0; overflow: hidden;">
          <div class="progress-bar-fill" style="height: 100%; background: var(--primary); width: ${c.participant_count > 0 ? 50 : 0}%;"></div>
        </div>
      </div>
    `;
    campaignCards.appendChild(card);
  });

  // Re-insert builder after the selected card if one is open
  if (state.selectedCampaignId && !builderPanel.classList.contains("hidden")) {
    const selectedCard = campaignCards.querySelector(`[data-campaign-id="${state.selectedCampaignId}"]`);
    if (selectedCard) {
      selectedCard.insertAdjacentElement("afterend", builderPanel);
    } else {
      builderPanel.classList.add("hidden");
      state.selectedCampaignId = null;
    }
  }
}

async function openCampaign(id, cardElement) {
  state.selectedCampaignId = id;
  const campaign = await api(`/api/campaigns/${id}`);
  builderTitle.textContent = `Campaign Builder: ${campaign.name}`;

  // Insert builder directly after the clicked card inside the grid
  const targetCard = cardElement || campaignCards.querySelector(`[data-campaign-id="${id}"]`);
  if (targetCard) {
    targetCard.insertAdjacentElement("afterend", builderPanel);
  } else {
    campaignCards.appendChild(builderPanel);
  }
  builderPanel.classList.remove("hidden");
  resetDirty();
  builderPanel.scrollIntoView({ behavior: "smooth", block: "start" });

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
    throw new Error("Open a campaign first");
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
    showToast("Campaign created");
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
      await openCampaign(Number(id), btn.closest("article"));
      showToast("Campaign opened");
    } else if (btn.dataset.duplicate) {
      await api(`/api/campaigns/${id}/duplicate`, { method: "POST" });
      showToast("Campaign duplicated");
      await loadCampaignCards();
    } else if (btn.dataset.start) {
      await api(`/api/campaigns/${id}/start`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("Campaign started");
      await loadCampaignCards();
    } else if (btn.dataset.pause) {
      await api(`/api/campaigns/${id}/pause`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("Campaign paused");
      await loadCampaignCards();
    } else if (btn.dataset.resume) {
      await api(`/api/campaigns/${id}/resume`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("Campaign resumed");
      await loadCampaignCards();
    } else if (btn.dataset.stop) {
      await api(`/api/campaigns/${id}/stop`, { method: "POST" });
      if (state.selectedCampaignId === Number(id)) {
        state.execution = await api(`/api/campaigns/${id}/execution`);
        renderExecutionStatus();
        await loadAttempts(Number(id));
      }
      showToast("Campaign stopped");
      await loadCampaignCards();
    } else if (btn.dataset.deleteCampaign) {
      await api(`/api/campaigns/${id}`, { method: "DELETE" });
      if (state.selectedCampaignId === Number(id)) {
        builderPanel.classList.add("hidden");
        state.selectedCampaignId = null;
      }
      showToast("Campaign deleted");
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
    resetDirty();
    showToast("Calling policy saved");
  } catch (err) {
    showToast(err.message);
  }
});

refreshAttemptsButton.addEventListener("click", async () => {
  try {
    const campaignId = getSelectedCampaignId();
    await loadAttempts(campaignId);
    showToast("Attempts refreshed");
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
    showToast("Question added");
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
    showToast("Questions updated");
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
    showToast("Rule added");
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
    showToast("Rule deleted");
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
    showToast("Participants uploaded");
  } catch (err) {
    showToast(err.message);
  }
});

loadCampaignCards().catch((err) => {
  showToast(err.message);
});
