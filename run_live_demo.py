"""
Run realistic demo voice sessions via the real API endpoints.
Each turn goes through /voice/sessions/{sid}/turn so the DB is fully updated.
"""
import asyncio
import json
import random
import urllib.request
from datetime import datetime

BASE = "http://localhost:8003"
rng = random.Random(42)

ANSWERS_BY_TYPE = {
    "rating": [
        "אני נותן תשע", "הייתי אומר שמונה", "שבע", "עשר",
        "שש", "תשע מתוך עשר", "שמונה", "שלוש",
        "חמש", "ארבע", "שתיים", "עשרה",
    ],
    "mcq": [
        "א", "ב", "ג", "ד",
        "אפשרות א", "אפשרות ב",
        "yes", "no", "כן", "לא",
        "mobile app", "website",
    ],
    # Rich free-text answers that contain cross-survey information
    "free_text": [
        "השירות היה מצוין לחלוטין. הנציג עזר לי מאוד ופתר את הבעיה תוך כמה דקות. "
        "אני ממליץ בחום על השירות לכל מי שמחפש פתרון מקצועי ומהיר. "
        "בסך הכל נתתי ציון 9 מתוך 10 לחוויה הכללית שלי.",

        "לא הייתי מרוצה מהחוויה. ההמתנה הייתה ארוכה מאוד, כמעט 20 דקות. "
        "הנציג לא ידע לענות על השאלות שלי ואמר שיחזור אלי אבל לא חזר. "
        "לא הייתי ממליץ על השירות הזה לחברים שלי בשלב זה.",

        "חוויה טובה בסך הכל. השתמשתי באפליקציה הסלולרית שעובדת מעולה. "
        "הממשק ידידותי מאוד ונוח לשימוש גם לאנשים פחות טכנולוגיים. "
        "הייתי ממליץ על זה, אולי ציון 8 מתוך 10.",

        "הייתה בעיה טכנית בתחילה שגרמה לי תסכול רב. "
        "לא הצלחתי להתחבר למערכת במשך שעה שלמה. "
        "בסוף הכל נפתר אחרי שדיברתי עם התמיכה הטכנית שהייתה מקצועית מאוד. "
        "נתתי ציון 6 כי הבעיה הראשונית פגעה בחוויה.",

        "שירות מקצועי ומהיר מאוד. קיבלתי תשובה תוך פחות מדקה. "
        "השתמשתי באתר האינטרנט שהוא הנוח ביותר לדעתי. "
        "בהחלט אמליץ לחברים ולמשפחה שלי. ציון 10 בלי ספק.",

        "צריך לשפר את זמן התגובה של שירות הלקוחות. "
        "ממתינים יותר מדי זמן בטלפון. "
        "הנציג עצמו היה נחמד ועזר בסוף, אבל ההמתנה גרמה לי ללחץ. "
        "ציון 5 מתוך 10 עד שישפרו את הנושא הזה.",

        "מרוצה מאוד מהטיפול שקיבלתי. "
        "פתחתי פנייה דרך האפליקציה הסלולרית וקיבלתי מענה תוך שעה. "
        "הצוות מקצועי, אדיב ויודע את החומר. "
        "ציון 9, ממליץ בחום לכולם.",

        "חוויה ממוצעת, לא מיוחדת אבל גם לא רעה. "
        "השתמשתי בתמיכה בטלפון שעבדה בסדר. "
        "אולי ציון 6 או 7. לא בטוח שאמליץ לחברים כי יש אפשרויות טובות יותר בשוק.",

        "הפתרון שקיבלתי היה מצוין ומדויק לבעיה שלי. "
        "הנציג הבין את הצרכים שלי מיד ולא בזבז את הזמן שלי. "
        "פניתי דרך האתר ותוך יום עסקים קיבלתי תשובה מלאה. "
        "ציון 9 מתוך 10, כן אמליץ על השירות.",

        "התאכזבתי מהשירות. ציפיתי להרבה יותר לאור המחיר ששילמתי. "
        "השתמשתי באתר אבל הממשק לא ברור ומבלבל. "
        "לא אמליץ לאחרים ואני שוקל לעבור לספק אחר. ציון 3.",
    ],
}


def post(url, data=None):
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def fetch_campaign_questions(campaign_id: int) -> dict:
    """Return {question_key: question_type} map for a campaign."""
    try:
        questions = get(f"{BASE}/api/campaigns/{campaign_id}/questions")
        return {q["key"]: q["question_type"] for q in questions}
    except Exception:
        return {}


# Cache questions per campaign
_CAMPAIGN_QUESTIONS: dict[int, dict] = {}


def get_answer_for_question(campaign_id: int, question_key: str | None) -> str:
    """Pick an answer appropriate for the current question type."""
    if not question_key:
        return rng.choice(ANSWERS_BY_TYPE["free_text"])

    if campaign_id not in _CAMPAIGN_QUESTIONS:
        _CAMPAIGN_QUESTIONS[campaign_id] = fetch_campaign_questions(campaign_id)

    qtype = _CAMPAIGN_QUESTIONS[campaign_id].get(question_key, "free_text")
    return rng.choice(ANSWERS_BY_TYPE[qtype])


async def run_session(campaign_id: int, phone: str, delay: float, idx: int):
    """Start a session and drive it turn-by-turn via the real API."""
    await asyncio.sleep(delay)

    try:
        r = post(f"{BASE}/api/campaigns/{campaign_id}/voice/sessions",
                 {"participant_phone": phone, "locale": "he-IL"})
        sid = r["session_id"]
    except Exception as e:
        print(f"[{phone}] start error: {e}")
        return

    print(f"[{idx:02d}] started session {sid[:8]} campaign={campaign_id}")

    current_question_key = r.get("current_question_key")

    for turn_num in range(20):
        await asyncio.sleep(rng.uniform(1.5, 3.0))

        transcript = get_answer_for_question(campaign_id, current_question_key)

        if rng.random() < 0.15:
            transcript = "אממ... " + transcript

        try:
            result = post(
                f"{BASE}/api/campaigns/{campaign_id}/voice/sessions/{sid}/turn",
                {
                    "transcript": transcript,
                    "audio_duration_ms": len(transcript.split()) * rng.uniform(300, 700),
                    "mic_hesitation_count": rng.randint(0, 2) if "אממ" in transcript else 0,
                },
            )
            action = result.get("dialogue_action", "?")
            current_question_key = result.get("current_question_key")
            print(f"[{idx:02d}] turn {turn_num+1}: {action} q={current_question_key}")

            if result.get("session_complete"):
                print(f"[{idx:02d}] DONE - session completed!")
                break
        except Exception as e:
            print(f"[{idx:02d}] turn error: {e}")
            break


async def main():
    CAMPAIGNS = [13, 6, 15, 18]
    N_SESSIONS = 30

    # Pre-fetch questions for all campaigns
    for cid in CAMPAIGNS:
        _CAMPAIGN_QUESTIONS[cid] = fetch_campaign_questions(cid)
        print(f"Campaign {cid} questions: {list(_CAMPAIGN_QUESTIONS[cid].items())}")

    print(f"\nStarting {N_SESSIONS} sessions across campaigns {CAMPAIGNS}...")

    tasks = []
    for i in range(N_SESSIONS):
        campaign = CAMPAIGNS[i % len(CAMPAIGNS)]
        phone = f"+972-50-{5000000 + i:07d}"
        delay = i * 1.0
        tasks.append(run_session(campaign, phone, delay, i))

    await asyncio.gather(*tasks)
    print("\nAll sessions finished!")

    try:
        kpis = get(f"{BASE}/api/dashboard/kpis?period=today")
        print(f"\nToday results:")
        print(f"  Total calls:      {kpis['total_calls']}")
        print(f"  Completed:        {kpis['completed']}")
        print(f"  Completion rate:  {kpis['completion_rate']}%")
        print(f"  Avg duration:     {kpis['avg_duration_seconds']}s")
    except Exception as e:
        print(f"KPIs error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
