# SurveyAgentAI

An AI-driven voice survey platform: companies create opt-in phone surveys, an AI agent ("Alex") conducts the actual conversation over a real phone call (via Twilio), adapts its delivery to the caller in real time, and the results feed a full analytics/intelligence layer — sentiment, named entities, fact-checking, cross-survey fact matching, and bias-aware demographic weighting. Multi-tenant (each company/organization only sees its own campaigns and data), with role-based access control and a human-in-the-loop operator console for escalated calls.

Every decision the agent makes — what the caller meant, whether to accept an answer, when to escalate to a human — is **rule-based**: keyword/regex matching, confidence thresholds, and deterministic scoring formulas. There is no external language model in the loop.

## Architecture Overview

```
Caller's phone
      │  (real call)
      ▼
Twilio  ──webhooks──▶  app/telephony/router.py
      │                       │
      │                       ▼
      │              app/voice/pipeline.py  (VoicePipeline.process_turn)
      │                 STT → NLU/decision layer → Dialogue FSM → Mirroring → TTS → Escalation check
      │                       │
      │              ┌────────┴────────┐
      │              ▼                 ▼
      │      app/voice/agent/    app/voice/dialogue/
      │      (rule-based          (state machine + skip-logic
      │       decision layer)      branch rules)
      │                       │
      ▼                       ▼
 Twilio <Say>/<Gather>   CallLog.status written synchronously
 (mirrored rate/pitch)   (race-safe against Twilio's own status webhook);
                         answers / history / cross-survey matching
                         persisted in a background thread
                                │
                                ▼
                    Postgres (Neon, cloud) ── app/models.py
                                │
                                ▼
              app/intelligence/*  (NER, sentiment, fact-check,
                                   cross-survey fact matching)
                                │
                                ▼
              app/analytics/*, app/dashboard/*  ──▶  static/*.html dashboards
```

Speech recognition and speech synthesis are entirely Twilio's own: `<Gather input="speech">` transcribes the caller live and returns text + a confidence score; `<Say>` (wrapped in an SSML `<prosody>` tag carrying the mirroring engine's computed rate/pitch) speaks the agent's replies. `app/voice/stt/` and `app/voice/tts/` define an adapter interface for a real STT/TTS vendor to be plugged in later, but only `Mock` implementations exist — for a live call they're used purely as a text pass-through (Twilio's already-transcribed text gets stuffed in before each turn), never for actual audio processing.

A background auto-dial scheduler (`app/main.py: _scheduler_loop`) ticks every 5 seconds, dialing campaign participants while respecting each campaign's `CallingPolicy` (calling-hours window, retry delay, cooldown, calls-per-minute cap). Three other background loops run alongside it — a DB keep-alive, a stale-session sweep, and a Twilio call-state watchdog — see [Background Maintenance Loops](#background-maintenance-loops) below. An operator console (`app/operator/`) lets a human take over any call the AI escalates, via a live WebRTC conference bridge.

## What Is Implemented

**Campaign management**
- Campaign CRUD (name, language, timezone, consent text), duplicate/pause/resume/start/stop lifecycle.
- Question Builder: `rating` / `mcq` / `free_text` questions, reorderable.
- Skip Logic: branch-rule engine (`goto` / `end` / `escalate`), rules evaluated in priority order, with a validated 1:1 correspondence between `action` and whether a target question is required.
- Participant CSV upload with opt-in tracking.

**Real voice calls (Twilio)**
- Outbound dialing via Twilio, TwiML `<Gather>`/`<Say>` conversation flow, SSML `<prosody>` for mirrored delivery. Hebrew requires an explicit Google-engine voice name (`Google.he-IL-Standard-A`) or Twilio silently speaks nothing.
- Auto-dial scheduler enforcing per-campaign calling policy: calling-hours window (converted to the campaign's own timezone), a rolling per-minute rate budget, and per-participant retry eligibility that factors in max attempts, retry delay, and a longer cooldown after a completed call — with a caller-requested callback time (parsed from free text, e.g. "call me tomorrow evening") honored instead of the generic delay when one was given. Consecutive outbound dials in the same tick are staggered by 3 seconds — placing two calls back-to-back was empirically found to make Twilio falsely report the first one as "no-answer" even when it was actually picked up.
- The scheduler's own DB work runs in a dedicated background thread (not on the main event loop) — a single tick against the cloud database was measured at 11+ seconds, which previously froze every other request the server was handling (including live Twilio webhooks) for that entire duration.
- Do-Not-Call list checked before every dial (both at the scheduler level and the manual call-initiation API).
- Call status webhook handling that distinguishes a normal completion from "call me back later" (`not_now`) without being overwritten by Twilio's own generic status callback.

**The AI interviewer (fully rule-based)**
- A layered decision pipeline (`app/voice/agent/`, `app/voice/nlu/`) interprets the caller's reply each turn: deterministic pre-checks for escalation requests, profanity, navigation questions, and pace requests run first (regex-matched, handled immediately, in any language), then a keyword-scored intent classifier (word-boundary matching, phrase-length-weighted confidence) and structured answer extraction (numeric ratings including spelled-out numbers, MCQ letters/ordinals in Hebrew and English) handle everything else.
- Confidence-gated answer acceptance: ≥0.90 auto-accepts an answer, 0.60–0.90 reads it back for confirmation, below 0.60 is treated as unclear and retried — up to 3 times before escalating.
- Dialogue FSM (`app/voice/dialogue/`) driving question flow, confirmation of uncertain answers, and skip-logic branching — including bidirectional resolution between an MCQ answer's stored option text and its letter, so a branch rule authored against either form works.
- Structured field validation for auto-asked intake questions (age: numeric range 1–120; city: rejects obvious non-answers like "I don't know" rather than checking against a closed list, since a caller from an unlisted small town is still a valid answer).
- Escalation engine (`app/voice/escalation/`) — a fixed, priority-ordered set of trigger rules (explicit request → max retries → repeated-unclear streak → high hesitation/distress → sustained low transcription confidence), each escalation scored on a weighted urgency formula (severity by reason, plus bonuses for low rapport/high hesitation/repeat escalations, minus a small penalty for calls that are almost finished) and pushed onto a thread-safe max-priority queue (heap-based, with O(1) removal-by-key via lazy tombstoning) for the operator console.
- **Psycho-Adaptive Voice Mirroring** (`app/voice/mirroring/`) — the agent's speaking rate and pitch adapt turn-by-turn to a calibrated baseline of the caller's own delivery: an exponential moving average (α = 0.6, reacting within 1–2 turns) locks a personal baseline after the caller's first turn, and every subsequent turn's rate/pitch is computed *relative to that baseline* (rate ±35%, pitch ±2 semitones, both hard-clamped), with a rapport-based kill switch reverting to neutral delivery if transcription confidence for the session drops below 0.5. See [Academic Basis](#academic-basis-for-voice-mirroring) below.

**Human-in-the-loop operator console**
- Live escalation queue sorted by urgency score, one-click takeover into a real Twilio conference call (WebRTC via `Twilio.Device`, both the operator's browser leg and the caller's phone leg joined into the same `<Conference>` room), return-to-agent (which resumes the dialogue's current question rather than restarting), hangup, full transcript view, and an audit trail of operator actions.

**Auth, multi-tenancy & compliance**
- Session-cookie auth with RBAC (`admin` / `operator` / `analyst`), passwords hashed with PBKDF2-HMAC-SHA256 (200,000 iterations).
- Self-service signup creates a new `Organization` + admin user; every campaign, user, and audit entry is scoped to its organization — no cross-tenant data leakage (enforced at the query level, not just in application logic).
- Encrypted provider-credential storage (Fernet symmetric encryption) for Twilio/STT/TTS API keys, a Do-Not-Call list, and a persistent settings audit log.

**Post-call intelligence** (`app/intelligence/`) — rule-based throughout
- Named entity recognition, free-text sentiment analysis (keyword-based positive/negative word sets across 5 topic categories, in Hebrew and English), and answer fact-checking: numeric ratings are range-checked against the question's configured bounds, MCQ answers are checked against the configured option list, and free-text claims are honestly reported as "not checkable" rather than guessed at.
- Interviewee profiles: every answer links to a persistent per-phone-number profile across campaigns.
- **Cross-survey fact matching**: if a caller states a numeric value in a free-text answer (digit or spelled-out, cardinal or ordinal, Hebrew or English — "seven hours" / "שבע שעות" both work) in one survey, the surrounding words get stripped of grammatical filler ("how many", "did you", "כמה", "האם"...) and compared against every question's own wording in every *other campaign in the same organization*; a match of 2+ overlapping meaningful words materializes as a real answer there — entirely generic (no hardcoded topic list, so a brand-new question created in the UI works automatically), hard-scoped by organization at the query level, and runs automatically the moment a call ends (a one-off backfill script, `run_cross_survey.py`, exists for pre-existing data from before this ran automatically).
- Demographic bias weighting: campaign target percentages vs. actual observed distribution per demographic bucket, reweighted as `target% / actual%`; falls back to showing the raw observed distribution (no weight) if no targets have been set yet, rather than an empty chart.

**Analytics & dashboards**
- Campaign-level and global analytics (completion trends, answer-quality-by-question, demographic bias, auto-generated insights, cross-survey match counts).
- Simple threshold-based anomaly detection on the call-duration/transcription-quality scatter (duration under 30 seconds, or quality below a configurable threshold) — not a statistical model.
- Live KPI dashboard, live-calls view (now correctly reflecting truly-active calls only — see below), call-outcome breakdowns.

## Background Maintenance Loops

Four `asyncio` tasks run for the lifetime of the app (started/cancelled together in `main.py`'s `lifespan`), each solving a distinct reliability problem:

- **Auto-dial scheduler** (5s tick) — described above.
- **DB keep-alive** (60s tick, active only if there's been real HTTP traffic in the last 10 minutes) — Neon (and similar serverless Postgres) suspends its compute after a few idle minutes, and the first query afterward pays a multi-second cold-start penalty; a fixed-timer ping would keep the compute alive 24/7 and burn through free-tier compute-hour quota, so this only pings during genuinely active stretches and lets Neon suspend normally overnight/on weekends. `pool_pre_ping` is also enabled on the SQLAlchemy engine, which validates a connection before use and transparently reconnects if Neon already closed it server-side. Live traffic runs through Neon's pooled (PgBouncer) endpoint; a separate direct endpoint handles schema migrations the pooler can't run.
- **Stale voice-session sweep** (2-minute tick, 15-minute idle threshold) — the live-calls dashboard reads from an in-memory session dict that previously only ever got cleaned up when a call reached a natural closing state; an escalated call, a dropped connection, or any call that didn't cleanly finish would sit there marked "active" indefinitely (observed in practice: a session showing 2.5 hours of "live" duration with no rapport data at all, meaning it had died immediately after starting). Every Twilio webhook hit for a session — including empty-`Gather` retries when the caller says nothing — now stamps a last-touch timestamp; since Twilio's own `<Gather timeout=8>` guarantees a webhook hit at least every ~8–10 seconds for any call that's genuinely still connected, a session going 15 minutes with zero webhook activity at all is a reliable dead-call signal, not a slow talker. Swept sessions are marked complete in memory and their `CallLog` status updated to `failed` in the database. Like the scheduler, its DB work runs in a background thread so it can never block live request handling.
- **Telephony watchdog** (`app/telephony/session_store.py`) — monitors in-flight `TelephonySession` state for calls that never receive a Twilio status callback.

## Project Structure

```text
app/
  main.py                 # Central FastAPI app: routing, background scheduler,
                           #   DB keep-alive + stale-session sweep loops,
                           #   ad-hoc startup migrations, voice-turn processing endpoint
  database.py              # SQLAlchemy engines (pooled runtime + direct migration),
                           #   UTC timezone handling
  models.py                 # 24 SQLAlchemy models (campaigns, calls, answers,
                           #   intelligence, auth/multi-tenancy, compliance)
  schemas.py               # Pydantic request/response contracts

  auth/                    # Session-cookie auth + RBAC, self-service org signup
  settings/                # Provider credentials (encrypted), DNC list, audit log
  telephony/               # Twilio integration: gateway, webhooks, TwiML,
                           #   WebRTC operator takeover, call-state tracking

  voice/                   # The voice AI pipeline
    pipeline.py            #   orchestrates one turn end-to-end
    agent/                 #   rule-based decision layer (deterministic pre-checks
                           #   + fallback intent/answer logic)
    dialogue/               #   conversation state machine + skip-logic
    escalation/             #   human-handoff trigger/scoring/priority queue
    mirroring/               #   psycho-adaptive rate/pitch adaptation
    nlu/                    #   keyword intent classifier, structured-field
                           #   validators (age/city), callback-time parser
    stt/ tts/                #   speech I/O adapter interface — mock implementations
                           #   only; the real audio path is Twilio's own Gather/Say

  intelligence/            # Post-call NLP: NER, sentiment, fact-check,
                           #   interviewee profiles, cross-survey matching
  analytics/               # Campaign + global analytics/reporting API
  dashboard/               # KPI + live-calls dashboard API
  operator/                # Human-in-the-loop console backend

  static/                  # index.html (campaign builder), analytics.html,
                           #   dashboard.html, operator.html, login/signup.html,
                           #   voice.html/simulator.html (browser demo),
                           #   settings.html, gallery.html — vanilla HTML/CSS/JS,
                           #   no frontend framework

tests/                     # pytest suite — campaigns API, auth/RBAC, org signup,
                           #   settings/DNC, scheduler, not_now callback,
                           #   telephony, voice pipeline (mirroring/agent/
                           #   escalation/operator/dialogue/branching)

scripts/migrate_to_cloud.py  # One-off Postgres → Neon cloud DB migration
run_cross_survey.py          # Manual backfill for cross-survey matching on
                           #   pre-existing data (new calls match automatically)
requirements.txt / requirements-dev.txt
```

### Frontend

No frontend framework — every page (`app/static/*.html`) is static HTML/CSS with vanilla JavaScript, served directly by FastAPI (`app.mount("/static", ...)`). Two third-party libraries are loaded via CDN:
- **Chart.js** v4.4.0 — renders the analytics dashboard's charts.
- **Twilio Voice JS SDK** v2 — WebRTC calling for the operator console's browser-based takeover.

Google Fonts ("Heebo", for Hebrew + Latin text) is also loaded via CDN.

## Run Locally

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

3. Create a `.env` file (see [Environment Variables](#environment-variables) below).

4. Start the app:

```bash
uvicorn app.main:app --reload
```

5. Open:

- `http://127.0.0.1:8000` — campaign builder dashboard
- `http://127.0.0.1:8000/static/signup.html` — create an account (first signup on an empty DB also works via the bootstrap admin)
- `http://127.0.0.1:8000/static/analytics.html` — analytics
- `http://127.0.0.1:8000/static/operator.html` — operator console
- `http://127.0.0.1:8000/docs` — API docs

For real outbound phone calls, Twilio must be configured (below) and `TWILIO_WEBHOOK_BASE_URL` must point to a publicly reachable URL for your local server (e.g. an ngrok tunnel) — **without a trailing slash**, or Twilio's webhook requests resolve to a double-slash path (`//api/telephony/webhook/...`) that doesn't match any route and Twilio plays a generic "application error" message instead of the call actually working.

## Environment Variables

```
DATABASE_URL=                   # Postgres connection string (Neon or any Postgres);
                                 #   falls back to local sqlite if unset
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_WEBHOOK_BASE_URL=         # public URL Twilio can reach (e.g. ngrok) — no trailing slash
TWILIO_API_KEY=
TWILIO_API_SECRET=
TWILIO_TWIML_APP_SID=            # needed for the browser WebRTC operator console
ADMIN_EMAIL=                    # optional — bootstrap admin (default admin@example.com)
ADMIN_PASSWORD=                 # optional — bootstrap admin (default changeme123)
SETTINGS_ENCRYPTION_KEY=         # optional — key for encrypting stored provider credentials;
                                 #   auto-generated and persisted to a local file if unset
```

`VONAGE_*` variables are read by `app/telephony/config.py` but there is currently no active Vonage gateway wired up — Twilio is the sole active telephony provider.

## Key API Endpoints

**Campaigns**: `POST /api/campaigns` · `GET /api/campaigns/summary` · `POST /api/campaigns/{id}/{duplicate,start,pause,resume,stop}` · `GET/PUT /api/campaigns/{id}/policy` · `GET /api/campaigns/{id}/attempts` · `POST /api/campaigns/{id}/questions[/reorder]` · `POST /api/campaigns/{id}/rules` · `POST /api/campaigns/{id}/participants/upload`

**Voice pipeline**: `POST /api/campaigns/{id}/voice/sessions` (start) · `POST .../voice/sessions/{session_id}/turn` (process one turn) · `GET/DELETE .../voice/sessions/{session_id}`

**Telephony**: `POST /api/telephony/calls` · `POST /api/telephony/webhook/{voice,gather,resume,status}` (Twilio webhooks) · `POST /api/telephony/conference-twiml` · `GET /api/telephony/token` (WebRTC access token)

**Auth**: `POST /api/auth/{login,logout,signup}` · `GET /api/auth/me` · `POST /api/auth/users` (admin)

**Settings**: `GET/PUT /api/settings/providers` · `GET/POST/DELETE /api/settings/dnc` · `GET /api/settings/audit`

**Analytics**: `GET /api/campaigns/{id}/analytics/summary` · `GET /api/analytics/{overview,completion-trend,anomaly-scatter,mirroring-effect,answer-quality,demographic-bias,insights,intelligence-summary,cross-survey-matches}`

**Dashboard / Operator**: `GET /api/dashboard/{kpis,live-calls,charts/*}` · `GET /api/operator-queue` · `POST /api/sessions/{session_id}/handover`

## CSV Upload Format

```csv
phone_number,full_name,locale
+15551234567,Alex Doe,en-US
+447700900123,Sam Lee,en-GB
```

## Academic Basis for Voice Mirroring

The mirroring feature (`app/voice/mirroring/`) — adapting the agent's speaking rate and pitch turn-by-turn to the caller's own calibrated baseline — is grounded in:

> Lubold, N., & Pon-Barry, H. (2014). **"Acoustic-Prosodic Entrainment and Rapport in Collaborative Learning Dialogues."** *Proceedings of the 2014 ACM Workshop on Multimodal Learning Analytics Workshop and Grand Challenge.* https://dl.acm.org/doi/10.1145/2666633.2666635

This study analyzed a corpus of collaborative dialogues and found that (1) speakers' acoustic-prosodic entrainment — unconsciously converging on each other's pitch, loudness, and speaking rate — correlates with rapport, (2) entrainment happens **turn-by-turn** rather than as a long-term average, and (3) **pitch** is the single most significant prosodic feature tied to rapport, ahead of speaking rate or loudness.

This directly informed the design of `app/voice/mirroring/`: `calibration.py` recomputes a smoothed baseline every turn (not just once at call start), and `policy.py` weights pitch adaptation as a primary channel alongside speaking rate — rather than mirroring being an arbitrary "make the voice feel nicer" heuristic, it follows an empirically-supported turn-by-turn entrainment model from spoken dialogue systems research.

In practice, pitch has far less usable dynamic range than speaking rate: rate is computed *relative to the caller's own baseline* (so it tracks real changes in their pace), while pitch is derived as a proxy from raw STT transcription confidence, which tends to stay in a narrow high band for clearly-recognized speech — so pitch can end up nearly constant for an entire call even while rate visibly varies turn to turn. This is a known characteristic of the current implementation, not a bug in the formula itself.

Broader theoretical context for *why* mirroring builds rapport at all:
- Howard Giles' **Communication Accommodation Theory** — the social-psychological theory of why speakers converge toward each other's speech style to build closeness/identification, including a modern application specifically to chatbots/conversational agents.
- Chartrand & Bargh (1999), **"The Chameleon Effect"** — the foundational (and most-cited) psychology paper establishing that unconscious mimicry increases liking and interaction smoothness between people, cited as the psychological basis by much of the vocal-entrainment literature.
