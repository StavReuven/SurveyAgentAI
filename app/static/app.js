const state = {
  selectedCampaignId: null,
  questions: [],
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

const jsonHeaders = { "Content-Type": "application/json" };

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 1800);
}

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

async function loadCampaignCards() {
  const campaigns = await api("/api/campaigns/summary");
  campaignCards.innerHTML = "";
  if (!campaigns.length) {
    campaignCards.innerHTML = "<p>No campaigns yet. Create your first one above.</p>";
    return;
  }

  campaigns.forEach((c) => {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3>${c.name}</h3>
      <div class="meta">${c.language} | ${c.timezone} | status: <strong>${c.status}</strong></div>
      <div class="meta">Questions: ${c.question_count} | Participants: ${c.participant_count}</div>
      <div class="actions">
        <button data-open="${c.id}">Open Builder</button>
        <button data-duplicate="${c.id}" class="alt">Duplicate</button>
        <button data-pause="${c.id}" class="alt">Pause</button>
        <button data-resume="${c.id}" class="alt">Resume</button>
        <button data-delete-campaign="${c.id}" class="warn">Delete</button>
      </div>
    `;
    campaignCards.appendChild(card);
  });
}

async function openCampaign(id) {
  state.selectedCampaignId = id;
  const campaign = await api(`/api/campaigns/${id}`);
  builderTitle.textContent = `Campaign Builder: ${campaign.name}`;
  builderPanel.classList.remove("hidden");

  state.questions = await api(`/api/campaigns/${id}/questions`);
  renderQuestions();

  const rules = await api(`/api/campaigns/${id}/rules`);
  renderRules(rules);

  const participants = await api(`/api/campaigns/${id}/participants`);
  renderParticipants(participants);
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
    btn.dataset.pause ||
    btn.dataset.resume ||
    btn.dataset.deleteCampaign;
  if (!id) {
    return;
  }

  try {
    if (btn.dataset.open) {
      await openCampaign(Number(id));
      showToast("Campaign opened");
    } else if (btn.dataset.duplicate) {
      await api(`/api/campaigns/${id}/duplicate`, { method: "POST" });
      showToast("Campaign duplicated");
      await loadCampaignCards();
    } else if (btn.dataset.pause) {
      await api(`/api/campaigns/${id}/pause`, { method: "POST" });
      showToast("Campaign paused");
      await loadCampaignCards();
    } else if (btn.dataset.resume) {
      await api(`/api/campaigns/${id}/resume`, { method: "POST" });
      showToast("Campaign resumed");
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
