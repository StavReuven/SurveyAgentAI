# SurveyAgentAI

VoiceSurvey AI `Survey Management (Campaign Builder)` MVP for creating and managing opt-in voice survey campaigns.

## What Is Implemented

- Campaign creation and management: name, language, timezone, consent text.
- Campaign cards with quick actions: open, duplicate, pause, resume, delete.
- Scheduling and Campaign Execution: start, pause, resume, stop lifecycle.
- Calling Policies: per-campaign windows, retry delay, cooldown, and calls-per-minute limit.
- Background scheduler loop that applies policy constraints to eligible participants.
- Question Builder: add/list/delete/reorder questions (`rating`, `mcq`, `free_text`).
- Skip Logic / Branching: rule engine CRUD (`goto`, `end`, `escalate`) with priority.
- Opt-in participants CSV upload and listing.
- SQLite persistence via SQLAlchemy.
- FastAPI backend and a responsive web dashboard.

## Project Structure

```text
app/
	database.py          # SQLAlchemy engine/session
	models.py            # Campaign, Question, BranchRule, Participant
	schemas.py           # Pydantic request/response contracts
	main.py              # FastAPI routes + static hosting
	static/
		index.html         # Campaign Builder UI
		styles.css         # Styling
		app.js             # UI logic
requirements.txt
```

## Run Locally

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the app:

```bash
uvicorn app.main:app --reload
```

4. Open:

- `http://127.0.0.1:8000` for the dashboard.
- `http://127.0.0.1:8000/docs` for API docs.

## Key API Endpoints

- `POST /api/campaigns`
- `GET /api/campaigns/summary`
- `POST /api/campaigns/{campaign_id}/duplicate`
- `POST /api/campaigns/{campaign_id}/start`
- `POST /api/campaigns/{campaign_id}/pause`
- `POST /api/campaigns/{campaign_id}/resume`
- `POST /api/campaigns/{campaign_id}/stop`
- `GET /api/campaigns/{campaign_id}/execution`
- `GET /api/campaigns/{campaign_id}/policy`
- `PUT /api/campaigns/{campaign_id}/policy`
- `GET /api/campaigns/{campaign_id}/attempts`
- `POST /api/campaigns/{campaign_id}/questions`
- `POST /api/campaigns/{campaign_id}/questions/reorder`
- `POST /api/campaigns/{campaign_id}/rules`
- `POST /api/campaigns/{campaign_id}/participants/upload`

## CSV Upload Format

Participants upload expects headers:

```csv
phone_number,full_name,locale
+15551234567,Alex Doe,en-US
+447700900123,Sam Lee,en-GB
```