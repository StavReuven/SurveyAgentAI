# -*- coding: utf-8 -*-
"""
Creates a longer Hebrew survey campaign with properly encoded questions,
then starts 3 voice sessions in demo-run mode with 7-second intervals.
Run AFTER the server is up: python create_hebrew_survey.py
"""
import json
import urllib.request
import urllib.error
import time

BASE = "http://127.0.0.1:8000"


def post(path, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read().decode("utf-8"))


def put(path, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="PUT",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


# ── 1. Create campaign ────────────────────────────────────────────────────────
print("יוצר קמפיין...")
campaign = post("/api/campaigns", {
    "name": "סקר חוויית לקוח - בדיקת מערכת",
    "language": "he",
    "timezone": "Asia/Jerusalem",
    "consent_text": "שיחה זו מוקלטת לצורכי מחקר ואיכות.",
})
cid = campaign["id"]
print(f"  קמפיין נוצר: ID={cid}")

# ── 2. Add 8 questions ────────────────────────────────────────────────────────
questions = [
    {
        "key": "q1",
        "prompt": "שלום! על סולם של 1 עד 10, כיצד תדרג את שביעות הרצון הכללית שלך מהשירות שקיבלת?",
        "question_type": "rating",
        "required": True,
        "config": {},
    },
    {
        "key": "q2",
        "prompt": "מה היה הנושא העיקרי של פנייתך? האם זה היה: א - תמיכה טכנית, ב - חיוב וחשבוניות, ג - מידע על מוצרים, ד - אחר?",
        "question_type": "mcq",
        "required": True,
        "config": {"choices": ["תמיכה טכנית", "חיוב וחשבוניות", "מידע על מוצרים", "אחר"]},
    },
    {
        "key": "q3",
        "prompt": "כמה זמן המתנת עד שנציג ענה לפנייתך? דרג מ-1 שהוא זמן קצר מאוד ועד 10 שהוא זמן ארוך מאוד.",
        "question_type": "rating",
        "required": True,
        "config": {},
    },
    {
        "key": "q4",
        "prompt": "האם הנציג שטיפל בך הבין את הבעיה שלך? א - כן, לחלוטין. ב - בחלקה. ג - לא ממש. ד - בכלל לא.",
        "question_type": "mcq",
        "required": True,
        "config": {"choices": ["כן, לחלוטין", "בחלקה", "לא ממש", "בכלל לא"]},
    },
    {
        "key": "q5",
        "prompt": "על סולם של 1 עד 10, עד כמה הנציג היה מקצועי ומנומס?",
        "question_type": "rating",
        "required": True,
        "config": {},
    },
    {
        "key": "q6",
        "prompt": "האם הבעיה שלך נפתרה בסיום השיחה? א - כן, הבעיה נפתרה לגמרי. ב - נפתרה חלקית. ג - לא נפתרה. ד - עדיין בטיפול.",
        "question_type": "mcq",
        "required": True,
        "config": {"choices": ["נפתרה לגמרי", "נפתרה חלקית", "לא נפתרה", "עדיין בטיפול"]},
    },
    {
        "key": "q7",
        "prompt": "על סולם של 1 עד 10, עד כמה סביר שתמליץ על השירות שלנו לחבר או לעמית?",
        "question_type": "rating",
        "required": True,
        "config": {},
    },
    {
        "key": "q8",
        "prompt": "האם יש לך הערה או הצעה לשיפור שתרצה לשתף אותנו? אנחנו מקשיבים.",
        "question_type": "free_text",
        "required": False,
        "config": {},
    },
]

print("מוסיף שאלות...")
for q in questions:
    result = post(f"/api/campaigns/{cid}/questions", q)
    print(f"  שאלה {q['key']} נוספה: {result.get('id')}")

# ── 3. Start campaign ─────────────────────────────────────────────────────────
print("מפעיל קמפיין...")
post(f"/api/campaigns/{cid}/start", {})
print("  קמפיין פעיל")

# ── 4. Add participants via CSV upload ────────────────────────────────────────
print("מוסיף משתתפים...")
phones = [
    ("+972521200001", "דנה לוי"),
    ("+972521200002", "יוסף כהן"),
    ("+972521200003", "מיכל אברהם"),
]

import io

csv_content = "phone_number,full_name,locale\n"
for phone, name in phones:
    csv_content += f"{phone},{name},he-IL\n"

csv_bytes = csv_content.encode("utf-8")

boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="participants.csv"\r\n'
    f"Content-Type: text/csv\r\n\r\n"
).encode("utf-8") + csv_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

req = urllib.request.Request(
    f"{BASE}/api/campaigns/{cid}/participants/upload",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
with urllib.request.urlopen(req) as r:
    upload_result = json.loads(r.read().decode("utf-8"))
print(f"  {upload_result.get('imported', len(phones))} משתתפים נוספו")

# ── 5. Start voice sessions ───────────────────────────────────────────────────
print("\nמתחיל שיחות קוליות...")
session_ids = []
for phone, name in phones:
    session = post(f"/api/campaigns/{cid}/voice/sessions", {
        "participant_phone": phone,
        "locale": "he-IL",
    })
    sid = session["session_id"]
    session_ids.append(sid)
    print(f"  שיחה התחילה: {name} → {sid}")
    time.sleep(1)

# ── 6. Start demo-run with 7s intervals ───────────────────────────────────────
print("\nמפעיל demo-run עם מרווח 7 שניות...")
for sid in session_ids:
    result = post(f"/api/sessions/{sid}/demo-run?interval=7", {})
    print(f"  {sid[:8]}... → {result.get('status')}")
    time.sleep(0.5)

print(f"""
═══════════════════════════════════════════════════════
 קמפיין: סקר חוויית לקוח - בדיקת מערכת  (ID: {cid})
 3 שיחות פעילות | 8 שאלות | מרווח 7 שניות בין תורות
═══════════════════════════════════════════════════════
 פתח בדפדפן:
   http://127.0.0.1:8000/static/voice.html
 ולחץ על אחת השיחות כדי לראות את התמלול בזמן אמת.
""")
