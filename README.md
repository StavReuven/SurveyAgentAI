# SurveyAgentAI

An AI-driven voice survey platform: companies create opt-in phone surveys, an AI agent ("Alex") conducts the actual conversation over a real phone call (via Twilio), adapts its delivery to the caller in real time, and the results feed a full analytics/intelligence layer — sentiment, named entities, fact-checking, cross-survey fact matching, and bias-aware demographic weighting. Multi-tenant (each company/organization only sees its own campaigns and data), with role-based access control and a human-in-the-loop operator console for escalated calls.

## Architecture Overview

```
Caller's phone
      │  (real call)
      ▼
Twilio  ──webhooks──▶  app/telephony/router.py
      │                       │
      │                       ▼
      │              app/voice/pipeline.py  (VoicePipeline.process_turn)
      │                 STT → NLU → Dialogue FSM → Mirroring → TTS → Escalation check
      │                       │
      │              ┌────────┴────────┐
      │              ▼                 ▼
      │      app/voice/agent/    app/voice/dialogue/
      │      (Claude / rule       (state machine + skip-logic
      │       fallback)            branch rules)
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

A separate background scheduler (`app/main.py: _scheduler_loop`) auto-dials campaign participants on a timer, respecting each campaign's `CallingPolicy` (calling-hours window, retry delay, cooldown, calls-per-minute cap). An operator console (`app/operator/`) lets a human take over any call the AI escalates, via a live WebRTC conference bridge.

## What Is Implemented

**Campaign management**
- Campaign CRUD (name, language, timezone, consent text), duplicate/pause/resume/start/stop lifecycle.
- Question Builder: `rating` / `mcq` / `free_text` questions, reorderable.
- Skip Logic: branch-rule engine (`goto` / `end` / `escalate`, prioritized, operator-based conditions).
- Participant CSV upload with opt-in tracking.

**Real voice calls (Twilio)**
- Outbound dialing via Twilio, TwiML `<Gather>`/`<Say>` conversation flow, SSML `<prosody>` for mirrored delivery.
- Background auto-dial scheduler enforcing per-campaign calling policy (window, max attempts, retry/cooldown, rate limit).
- Do-Not-Call list checked before every dial.
- Call status webhook handling that distinguishes a normal completion from "call me back later" (`not_now`) without being overwritten by Twilio's own generic status callback.

**The AI interviewer**
- LLM-backed agent (`app/voice/agent/`, Claude via `ANTHROPIC_API_KEY`) that interprets the caller's reply each turn, with a full rule-based fallback (profanity/escalation/pace detection, answer extraction) when no API key is configured.
- Dialogue FSM (`app/voice/dialogue/`) driving question flow, confirmation of uncertain answers, and skip-logic branching.
- Escalation engine (`app/voice/escalation/`) scoring urgency (distress, anger, profanity, repeated confusion) and pushing a snapshot to a priority queue for a human operator.
- **Psycho-Adaptive Voice Mirroring** (`app/voice/mirroring/`) — the agent's speaking rate and pitch adapt turn-by-turn to a calibrated baseline of the caller's own delivery, with a rapport-based kill switch reverting to neutral. See [Academic Basis](#academic-basis-for-voice-mirroring) below.

**Human-in-the-loop operator console**
- Live escalation queue, one-click takeover into a real Twilio conference call, return-to-agent, hangup, full transcript view, and an audit trail of operator actions.

**Auth, multi-tenancy & compliance**
- Session-cookie auth with RBAC (`admin` / `operator` / `analyst`).
- Self-service signup creates a new `Organization` + admin user; every campaign, user, and audit entry is scoped to its organization — no cross-tenant data leakage.
- Encrypted provider-credential storage (Twilio/Anthropic/STT/TTS API keys), Do-Not-Call list, and a persistent settings audit log.

**Post-call intelligence** (`app/intelligence/`)
- Named entity recognition, free-text sentiment/topic/intent analysis, answer fact-checking (range/option validity, LLM claim-plausibility check) — rule-based by default, upgraded to Claude when an API key is present.
- Interviewee profiles: every answer links to a persistent per-phone-number profile across campaigns.
- **Cross-survey fact matching**: if a caller states a factual value (any number — "I got 7 hours of sleep") in one survey, it's automatically checked against every other question in every *other campaign in the same organization*, and materialized as a real answer there if the wording overlaps — entirely generic (no hardcoded topic list), scoped so it can never leak across organizations, and runs automatically the moment a call ends (no manual script needed for new calls).
- Demographic bias weighting for skewed samples.

**Analytics & dashboards**
- Campaign-level and global analytics (completion trends, anomaly detection, mirroring effectiveness, answer-quality-by-question, demographic bias, auto-generated insights, cross-survey match counts).
- Live KPI dashboard, live-calls view, call-outcome breakdowns.

## Project Structure

```text
app/
  main.py                 # Central FastAPI app: routing, background scheduler,
                           #   DB keep-alive loop, ad-hoc startup migrations,
                           #   voice-turn processing endpoint
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
    agent/                 #   LLM interviewer + rule-based fallback
    dialogue/               #   conversation state machine + skip-logic
    escalation/             #   human-handoff trigger/scoring/queue
    mirroring/               #   psycho-adaptive rate/pitch adaptation
    nlu/                    #   intent classification
    stt/ tts/                #   speech I/O adapters (real audio path is
                           #   Twilio's own Gather/Say; these back the
                           #   browser demo/simulator pages)

  intelligence/            # Post-call NLP: NER, sentiment, fact-check,
                           #   interviewee profiles, cross-survey matching
  analytics/               # Campaign + global analytics/reporting API
  dashboard/               # KPI + live-calls dashboard API
  operator/                # Human-in-the-loop console backend

  static/                  # index.html (campaign builder), analytics.html,
                           #   dashboard.html, operator.html, login/signup.html,
                           #   voice.html/simulator.html (browser demo),
                           #   settings.html, gallery.html

tests/                     # pytest suite — campaigns API, auth/RBAC, org signup,
                           #   settings/DNC, scheduler, not_now callback,
                           #   telephony, voice pipeline (mirroring/agent/
                           #   escalation/operator/dialogue/branching)

scripts/migrate_to_cloud.py  # One-off Postgres → Neon cloud DB migration
run_cross_survey.py          # Manual backfill for cross-survey matching on
                           #   pre-existing data (new calls match automatically)
requirements.txt / requirements-dev.txt
```

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

For real outbound phone calls, Twilio must be configured (below) and `TWILIO_WEBHOOK_BASE_URL` must point to a publicly reachable URL for your local server (e.g. an ngrok tunnel).

## Environment Variables

```
DATABASE_URL=                   # Postgres connection string (Neon or any Postgres);
                                 #   falls back to local sqlite if unset
ANTHROPIC_API_KEY=               # optional — enables the LLM agent + LLM-based
                                 #   intelligence upgrades; everything has a
                                 #   rule-based fallback without it
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
TWILIO_WEBHOOK_BASE_URL=         # public URL Twilio can reach (e.g. ngrok)
TWILIO_API_KEY=
TWILIO_API_SECRET=
TWILIO_TWIML_APP_SID=            # needed for the browser WebRTC operator console
ADMIN_EMAIL=                    # optional — bootstrap admin (default admin@example.com)
ADMIN_PASSWORD=                 # optional — bootstrap admin (default changeme123)
```

`VONAGE_*` variables are read by `app/telephony/config.py` but there is currently no active Vonage gateway wired up — Twilio is the sole active telephony provider.

## Key API Endpoints

**Campaigns**: `POST /api/campaigns` · `GET /api/campaigns/summary` · `POST /api/campaigns/{id}/{duplicate,start,pause,resume,stop}` · `GET/PUT /api/campaigns/{id}/policy` · `GET /api/campaigns/{id}/attempts` · `POST /api/campaigns/{id}/questions[/reorder]` · `POST /api/campaigns/{id}/rules` · `POST /api/campaigns/{id}/participants/upload`

**Voice pipeline**: `POST /api/campaigns/{id}/voice/sessions` (start) · `POST .../voice/sessions/{session_id}/turn` (process one turn) · `GET/DELETE .../voice/sessions/{session_id}`

**Telephony**: `POST /api/telephony/call` · `POST /api/telephony/webhook/{voice,gather,resume,status}` (Twilio webhooks) · `GET /api/telephony/access-token` (WebRTC)

**Auth**: `POST /api/auth/{login,logout,signup}` · `GET /api/auth/me` · `POST /api/auth/users` (admin)

**Settings**: `GET/PUT /api/settings/providers` · `GET/POST/DELETE /api/settings/dnc` · `GET /api/settings/audit`

**Analytics**: `GET /api/campaigns/{id}/analytics/summary` · `GET /api/analytics/{overview,completion-trend,anomaly-scatter,mirroring-effect,answer-quality,demographic-bias,insights,intelligence-summary,cross-survey-matches}`

**Dashboard / Operator**: `GET /api/dashboard/{kpis,live-calls,charts/*}` · `GET /api/operator/queue` · `POST /api/operator/{takeover,return,hangup}`

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

Broader theoretical context for *why* mirroring builds rapport at all:
- Howard Giles' **Communication Accommodation Theory** — the social-psychological theory of why speakers converge toward each other's speech style to build closeness/identification, including a modern application specifically to chatbots/conversational agents.
- Chartrand & Bargh (1999), **"The Chameleon Effect"** — the foundational (and most-cited) psychology paper establishing that unconscious mimicry increases liking and interaction smoothness between people, cited as the psychological basis by much of the vocal-entrainment literature.
