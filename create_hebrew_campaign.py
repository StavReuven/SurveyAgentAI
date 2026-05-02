# -*- coding: utf-8 -*-
import urllib.request, json

BASE = "http://localhost:8000"

def post(url, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

def put(url, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="PUT")
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

# 1. Create campaign
camp = post("/api/campaigns", {
    "name": "סקר שביעות רצון לקוחות 2026",
    "language": "he",
    "timezone": "Asia/Jerusalem",
    "consent_text": "שיחה זו מוקלטת לצורכי שיפור השירות. ההשתתפות מרצון."
})
cid = camp["id"]
print(f"קמפיין נוצר: #{cid}")

# 2. Add questions
questions = [
    {
        "key": "overall_rating",
        "prompt": "בסולם מ-1 עד 10, כמה אתה מרוצה מהשירות שקיבלת בכללותו?",
        "question_type": "rating",
        "required": True,
        "config": {}
    },
    {
        "key": "recommend",
        "prompt": "האם תמליץ על השירות שלנו לחבר או בן משפחה? ענה כן או לא.",
        "question_type": "free_text",
        "required": True,
        "config": {}
    },
    {
        "key": "channel",
        "prompt": "דרך איזה ערוץ פנית אלינו? א - טלפון, ב - אתר אינטרנט, ג - אפליקציה, ד - דואר אלקטרוני.",
        "question_type": "mcq",
        "required": True,
        "config": {"choices": ["טלפון", "אתר אינטרנט", "אפליקציה", "דואר אלקטרוני"]}
    },
    {
        "key": "wait_time",
        "prompt": "כמה היית מרוצה מזמן ההמתנה? דרג מ-1 עד 10.",
        "question_type": "rating",
        "required": True,
        "config": {}
    },
    {
        "key": "improvement",
        "prompt": "במשפט אחד, מה היית משנה כדי לשפר את חוויית השירות?",
        "question_type": "free_text",
        "required": False,
        "config": {}
    }
]

for q in questions:
    post(f"/api/campaigns/{cid}/questions", q)
    print(f"  שאלה נוספה: {q['key']}")

# 3. Activate
put(f"/api/campaigns/{cid}", {"status": "active"})
print("קמפיין הופעל")

# 4. Start 3 demo calls
import time
phones = ["+972-50-400-0001", "+972-50-400-0002", "+972-50-400-0003"]
for phone in phones:
    sess = post(f"/api/campaigns/{cid}/voice/sessions", {"participant_phone": phone})
    sid = sess["session_id"]
    post(f"/api/sessions/{sid}/demo-run", {})
    print(f"שיחה: {phone} -> {sid}")
    time.sleep(4)

print("סיום!")
