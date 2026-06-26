"""Rule-based fallback agent — used when the Claude API is unavailable."""

from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

from .schema import AgentDecision, AgentIntent, ExtractedAnswer, NextAction

if TYPE_CHECKING:
    from app.voice.dialogue.fsm import QuestionContext

# ── emotionally varied acknowledgements ──────────────────────────────────────
# High score / YES / positive
_ACKS_HIGH_EN = [
    "That's great to hear!", "Wonderful, really!", "Oh, love that!",
    "Excellent — so happy to hear that!", "Brilliant, thank you!",
    "That's really good to know!", "Awesome!", "That's fantastic!",
]
_ACKS_HIGH_HE = [
    "מצוין, שמח לשמוע!", "נהדר!", "זה ממש טוב לשמוע!", "כל הכבוד!",
    "מעולה, תודה!", "זה מרגש לשמוע, תודה רבה!", "מדהים!",
]
# Low score / NO / negative
_ACKS_LOW_EN = [
    "I'm sorry to hear that.", "Oh, that's below what we'd hope for — thank you for being honest.",
    "I understand, and I appreciate your candour.", "That's really useful to know.",
    "Thanks for being so direct — that kind of feedback really helps.",
]
_ACKS_LOW_HE = [
    "מצטער לשמוע.", "תודה על הכנות — זה מידע חשוב מאוד.", "מבין, ומעריך את הכנות שלך.",
    "זה חשוב שאמרת — תודה.", "לא מה שהיינו רוצים לשמוע, אבל חשוב לדעת.",
]
# Neutral / mid
_ACKS_MID_EN = [
    "Got it.", "Fair enough.", "I hear you.", "Makes sense.", "Noted.", "Appreciate it.",
]
_ACKS_MID_HE = [
    "הבנתי.", "בסדר.", "מבין.", "יפה.", "תודה.", "מעריך את זה.",
]

# ── varied terminal responses (Peak-End Rule) ─────────────────────────────────
_CLOSINGS_EN = [
    "That's the last one — thank you so much for your time, we really appreciate it!",
    "And we're all done! Your feedback means a lot, genuinely. Have a wonderful day!",
    "Perfect, that wraps it up! Thank you for sharing your thoughts — it helps us a lot.",
    "Great, we're finished! Really appreciate you taking the time. Take care!",
]
_CLOSINGS_HE = [
    "זהו, סיימנו! תודה רבה על זמנך — המשוב שלך חשוב לנו מאוד. שיהיה יום נפלא!",
    "מצוין, זו הייתה השאלה האחרונה. תודה שהשתתפת — זה עוזר לנו המון!",
    "תודה רבה על כל התשובות! אנחנו מעריכים את זה מאוד. המשך יום טוב!",
    "סיימנו! תודה שהקדשת מזמנך — תמיד שמח לשמוע ממך. להתראות!",
]
_OPT_OUT_EN = [
    "Of course — I'll remove you right away. Sorry for the interruption, take care!",
    "No problem at all. You're off our list immediately. Have a great rest of your day!",
    "Absolutely understood. I'll make sure we don't call again. Sorry to have bothered you!",
    "Got it — you're removed. We appreciate your honesty and won't contact you again.",
]
_OPT_OUT_HE = [
    "מובן, נסיר אותך מהרשימה מיד. מצטערים על ההפרעה — יום טוב!",
    "בסדר גמור, לא נתקשר יותר. תודה על הכנות ושיהיה יום נפלא!",
    "כמובן — אתה מוסר מהרשימה כבר עכשיו. מתנצלים שהפרענו.",
    "הבנתי, מסיר אותך מיד. מעריך את הכנות שלך — להתראות!",
]
_NOT_NOW_EN = [
    "Of course! We'll try again at a better time. Sorry for catching you at a bad moment!",
    "No worries at all — we'll call back when it's more convenient. Have a good one!",
    "Totally understand! We'll reach out later. Take care in the meantime!",
    "Sure thing — we'll find a better moment. Thanks for letting us know!",
]
_NOT_NOW_HE = [
    "בסדר, נתקשר שוב במועד נוח יותר. סליחה שתפסנו אותך בזמן לא מתאים!",
    "אין בעיה! נחזור אליך מאוחר יותר. שיהיה יום טוב!",
    "מבין לחלוטין — נתקשר בזמן מתאים יותר. תודה שאמרת לנו!",
    "כמובן, נמצא זמן מתאים יותר. תודה ולהתראות!",
]

# ── intent patterns ──────────────────────────────────────────────────────────

_OPT_OUT = re.compile(
    r"\b("
    # Explicit opt-out / stop phrases
    r"stop|opt.?out|call.?out"               # "opt out", "call out" (STT error)
    r"|remove me|unsubscribe|delete me|drop me"
    r"|do not call|don'?t call|never call"
    r"|leave me alone|take me off|take me out"
    r"|please stop|just stop|stop (calling|the survey|this)"
    r"|end (this|the call|the survey)"
    r"|i'?m done|we'?re done|i (want to )?quit|cancel"
    r"|hang up|goodbye forever"
    r"|no more (calls?|questions?|surveys?)"
    # Expressions of unwillingness to participate
    r"|don'?t want to( (do this|continue|participate|answer|go on))?"
    r"|do not want to( (do this|continue|participate|answer|go on))?"
    r"|i want (out|to (stop|end this|leave|quit))"
    r"|i'?m not (doing|answering|participating)"
    r"|refuse|i refuse"
    r"|not interested|i'?m not interested"
    r")\b",
    re.I,
)
_NOT_NOW = re.compile(
    r"\b("
    r"not now|not right now|not today"
    r"|call (me )?back|call me later|call again later"
    r"|later|i'?ll do it later|catch me later|try (me )?later|try again later"
    r"|i'?m busy|bad time|not a good time|i'?m in the middle"
    r"|another time|maybe later|some other time"
    r"|can('?t)? talk (now|right now)"
    r")\b",
    re.I,
)
_REPEAT = re.compile(
    r"\b(repeat|say (that )?again|what did you say|didn'?t hear|didn'?t catch"
    r"|come again|pardon|could you repeat|once more)\b"
    r"|^\s*what[?!]?\s*$",   # standalone "what" / "what?" only — not mid-sentence
    re.I | re.MULTILINE,
)
_REPHRASE = re.compile(
    r"\b(rephrase|replace (\w+ )?(the )?(\w+ )?question|different(ly)?|explain|don'?t understand|not sure what you mean"
    r"|clarify|what do you mean|can you explain|elaborate|i'?m confused"
    r"|remind me|remember me|what are (the |my )?(options|choices|channels|alternatives)"
    r"|what were (the )?(options|choices)|list (the |my )?(options|choices)"
    r"|מה האפשרויות|תזכיר לי|מה הבחירות)\b",
    re.I,
)
_PACE_RE = re.compile(
    r"\b(speak (slower|faster|more slowly|more quickly|up|down|quieter|louder)"
    r"|slow(er)?( down)?|speed up|too (fast|slow|quick|rapid)"
    r"|(a (bit|little) )?(slower|faster|more slowly|more quickly)"
    r"|can (you )?(slow|speed) (down|up)"
    r"|lower(?:( (your )?(speed|pace|voice|volume))?)"
    r"|דבר (יותר )?(לאט|מהר|בשקט|חזק)|מהר מדי|לאט מדי)\b",
    re.I,
)
_PACE_FASTER_RE = re.compile(
    r"\b(faster|more quickly|speed up|too slow(ly)?|quicker|pick up( the)? pace)\b", re.I
)
_PACE_SLOWER_RE = re.compile(
    r"\b(slow(er)?( down)?|slowly|lower|quieter|too (fast|quick|rapid)|לאט|בשקט)\b", re.I
)
_ESCALATE = re.compile(
    r"\b("
    # explicit human-transfer requests (English)
    r"speak (to|with) (a )?(human|person|agent|representative|supervisor|manager)"
    r"|talk (to|with) (a )?(human|person|agent|representative|supervisor|manager)"
    r"|connect me (to|with) (a )?(human|person|agent|representative|supervisor|manager)"
    r"|let me (speak|talk) (to|with) (a )?(human|person|supervisor|manager|real person)"
    r"|get me (a |your )?(supervisor|manager|human|person)"
    r"|i want (a |to (speak|talk) (to|with) a )?(supervisor|manager|human|real person)"
    r"|transfer( me)?( to)?"
    r"|supervisor|escalate"
    # anger patterns (English)
    r"|i'?m (angry|furious|livid)|this is (ridiculous|outrageous|unacceptable)"
    r"|stop wasting my time|waste of (my )?time|this is pointless"
    r"|you'?re (useless|terrible|awful|a robot)"
    r"|i hate this|terrible (service|experience)"
    # human-transfer requests (Hebrew)
    r"|תעביר אותי (למנהל|לנציג|לבן אדם|לאדם אמיתי)"
    r"|רוצה לדבר (עם מנהל|עם נציג|עם בן אדם|עם ממונה)"
    r"|אני רוצה (מנהל|נציג|בן אדם|ממונה)"
    r"|תחבר אותי (למנהל|לנציג|לבן אדם)"
    r"|דבר (עם )?מנהל|מנהל בבקשה|ממונה בבקשה"
    r"|תן לי (לדבר עם )?(מנהל|נציג|בן אדם|ממונה)"
    r"|העבר אותי|רוצה להעביר"
    r")\b",
    re.I,
)
_ESCALATE_MANAGER_RE = re.compile(
    r"\b(supervisor|manager)\b|ממונה|מנהל", re.I
)
_PROFANITY = re.compile(
    r"\b(fuck|shit|bitch|asshole|bastard|damn it|crap|dick|pussy|motherfucker"
    r"|wtf|go to hell|screw you|idiot|moron|stupid (bot|survey|call)"
    r"|כסח|לך תזדיין|בן זונה|מה הבאסה|זיין|שמות גידוף)\b",
    re.I,
)
_NAVIGATION_RE = re.compile(
    r"\b("
    r"what about the (first|previous|last|earlier|other) question"
    r"|can we go back|go back to|what happened to (the )?(first|previous|earlier)"
    r"|you skipped (a |the )?(first |previous )?question"
    r"|we (never |didn'?t )answered? (that|the first|the previous)"
    r"|what about (that|it)\??$"
    r"|מה עם השאלה (הראשונה|הקודמת|הקודם)|חזור לשאלה|דילגת על שאלה"
    r")\b",
    re.I,
)
_CONVERSATIONAL = re.compile(
    r"\b(what'?s? (?:is )?your name|what do (i|we|you) call you"
    r"|who are you|what are you"
    r"|are you (a )?(real( person)?|human|person|bot|ai|robot|machine|computer|virtual)"
    r"|am i (talking|speaking) to (a )?(human|real person|bot|ai|robot|machine|computer)"
    r"|are you (an? )?(artificial intelligence|ai|language model|llm|automated)"
    r"|you'?re? (a )?(bot|robot|ai|machine|computer|not (real|human))"
    r"|how are you( doing| today)?|how'?re you"
    r"|what (do you|would you) (think|recommend|suggest|say|prefer|pick|choose)"
    r"|what(?:'s?| is) (the )?(best|your favorite|your (pick|choice|preference|opinion|recommendation))"
    r"|what (is|are) (the )?(best( option)?|better option|right (option|choice|answer))"
    r"|which (is|would be) (the )?(best|better|your (pick|choice|preference))"
    r"|what is (the )?(recommendation|your recommendation|your opinion|your view)"
    # pace / speed requests
    r"|speak (slower|faster|more slowly|more quickly|up|down|quieter|louder)"
    r"|slow(er)?( down)?|speed up|too (fast|slow|quick)"
    r"|(a (bit|little) )?(slower|faster|more slowly|more quickly)"
    r"|can (you )?(slow|speak) (down|up|slower)"
    r"|lower(?:( (your )?(speed|pace|voice|volume))?)"
    r"|דבר (יותר )?(לאט|מהר|בשקט|חזק)|מהר מדי|לאט מדי"
    r"|מה שמך|מי אתה|מה אתה"
    r"|אתה (בוט|AI|מחשב|רובוט|אנושי|בן אדם|אמיתי|וירטואלי|אוטומטי)"
    r"|אני מדבר עם (מחשב|בוט|בן אדם|אוטומציה)"
    r"|זה (בוט|AI|מחשב|רובוט|אנושי)"
    r"|מה (הכי טוב|לדעתך|אתה ממליץ|תמליץ|היית בוחר))\b",
    re.I,
)
_NAME_RE = re.compile(
    r"\bwhat'?s? (?:is )?your name\b|מה שמך|איך קוראים לך", re.I
)
_ARE_YOU_AI_RE = re.compile(
    r"\bare you (a )?(bot|ai|robot|machine|computer|virtual|automated|not (a |real )?human)\b"
    r"|\bam i (talking|speaking) to (a )?(bot|ai|robot|machine|computer)\b"
    r"|\byou'?re? (a )?(bot|robot|ai|machine|computer)\b"
    r"|אתה (בוט|AI|מחשב|רובוט|וירטואלי|אוטומטי)\b"
    r"|זה (בוט|AI|מחשב|רובוט)\b",
    re.I,
)
_OPINION_RE = re.compile(
    r"\b(what (do you|would you) (think|recommend|suggest|say|prefer|pick|choose)"
    r"|what(?:'s?| is) (the )?(best|your (pick|choice|preference|opinion|recommendation))"
    r"|what (is|are) (the )?(best( option)?|better option|right (option|choice|answer))"
    r"|which (is|would be) (the )?(best|better|your (pick|choice|preference))"
    r"|what is (the )?(recommendation|your recommendation|your opinion|your view)"
    r"|מה (הכי טוב|לדעתך|אתה ממליץ|תמליץ|היית בוחר))\b",
    re.I,
)

# ── answer extraction patterns ────────────────────────────────────────────────

# Digit pattern — skip time-like formats (8:20, 12:30)
_TIME_PATTERN  = re.compile(r"\b\d{1,2}:\d{2}\b")
_DIGIT_PATTERN = re.compile(r"(?<!\d)(?<!\d:)\b(10|[1-9])\b(?!:\d)")

_RATING_WORDS: dict[str, int] = {
    # English cardinal numbers
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    # English sentiment mapped to 1-10 scale
    "terrible": 1, "awful": 1, "horrible": 1, "dreadful": 1,
    "bad": 2, "poor": 2, "disappointing": 2, "very bad": 2,
    "okay": 4, "ok": 4, "average": 5, "fine": 5, "alright": 5, "mediocre": 4, "so so": 4,
    "decent": 6, "not bad": 6, "fairly good": 6,
    "good": 7, "nice": 7, "well": 7, "pretty good": 7,
    "very good": 8, "great": 8, "quite good": 8,
    "excellent": 9, "wonderful": 9, "fantastic": 9, "brilliant": 9,
    "perfect": 10, "amazing": 10, "outstanding": 10, "superb": 10, "exceptional": 10,
    # Hebrew cardinal numbers (1–10)
    "אחד": 1, "אחת": 1,
    "שניים": 2, "שתיים": 2, "שתים": 2,
    "שלושה": 3, "שלוש": 3,
    "ארבעה": 4, "ארבע": 4,
    "חמישה": 5, "חמש": 5,
    "שישה": 6, "שש": 6,
    "שבעה": 7, "שבע": 7,
    "שמונה": 8,
    "תשעה": 9, "תשע": 9,
    "עשרה": 10, "עשר": 10,
    # Hebrew sentiment → approximate rating
    "נורא": 1, "גרוע": 1, "איום": 1,
    "רע": 2, "גרוע מאוד": 2,
    "בסדר": 3, "ממוצע": 3, "סביר": 3,
    "טוב": 4, "יפה": 4, "נחמד": 4,
    "מצוין": 5, "מעולה": 5, "מדהים": 5, "נהדר": 5, "פנטסטי": 5,
}
_YES = re.compile(
    r"\b(yes|yeah|yep|yup|sure|absolutely|correct|that'?s right|affirmative"
    r"|of course|definitely|certainly|indeed|i would|i'?d say yes|i think so"
    r"|i believe so|i suppose so|most likely|probably yes|i'?d recommend"
    r"|would recommend|i'?m happy to|glad to|i'?d be happy"
    r"|כן|בטח|בוודאי|נכון|אכן|בדיוק|כמובן|ודאי|הייתי ממליץ|אמליץ)\b",
    re.I,
)
_NO = re.compile(
    r"\b(no|nope|nah|not really|negative|incorrect|wrong|never|not at all"
    r"|i wouldn'?t|i don'?t think so|i don'?t believe so|probably not"
    r"|wouldn'?t recommend|i'?d rather not|i'?m not sure i would"
    r"|לא|ממש לא|בשום אופן|לא הייתי ממליץ|לא בטוח)\b",
    re.I,
)
_MCQ_LETTER   = re.compile(r"\b([a-dA-D])\b")
_MCQ_ORDINALS = {"first": "A", "one": "A", "second": "B", "two": "B",
                 "third": "C", "three": "C", "fourth": "D", "four": "D"}
_MCQ_ORDINAL_RE = re.compile(
    r"\b(first|second|third|fourth|one|two|three|four)\b", re.I
)
# Phonetic spoken letter names  ("bee" → B, "see/sea" → C, etc.)
_MCQ_PHONETIC: dict[str, str] = {
    "ay": "A", "bee": "B", "see": "C", "sea": "C", "dee": "D",
}
_MCQ_PHONETIC_RE = re.compile(r"\b(ay|bee|see|sea|dee)\b", re.I)


class RuleBasedFallback:
    """Deterministic rule-based agent decision for common survey intents."""

    def analyze(
        self,
        transcript: str,
        question: QuestionContext | None,
        language: str = "en",
        next_question: QuestionContext | None = None,
        history: list[dict] | None = None,
    ) -> AgentDecision:
        text = transcript.strip()
        lower = text.lower()
        q_type = question.question_type if question else "free_text"
        he = language.startswith("he")
        options: list[str] = (question.config or {}).get("options", []) if question else []

        # ── system resume signal (operator handed back to agent) ─────────────
        if lower.strip() == '[resume]':
            return AgentDecision(
                intent=AgentIntent.REPEAT_QUESTION,
                confidence=1.0,
                next_action=NextAction.REPEAT,
                response_text="",   # FSM will speak the current question
                should_save_answer=False,
                reason="operator returned control; re-ask current question",
            )

        # ── early answer extraction (before intent patterns) ─────────────────
        # For structured question types (rating/yes_no/mcq/numeric), if there is
        # a clear extractable answer we accept it immediately, even if the utterance
        # also contains a conversational phrase or stray "what" that would otherwise
        # trigger REPEAT/CONVERSATIONAL incorrectly.
        if q_type not in ("free_text",):
            _early_answer = self._extract(lower, text, q_type, options)
            if _early_answer:
                ack = self._pick_ack(_early_answer, q_type, he)
                return AgentDecision(
                    intent=AgentIntent.ANSWER,
                    confidence=0.80,
                    next_action=NextAction.CONTINUE,
                    response_text=self._build_transition(ack, next_question, he),
                    should_save_answer=True,
                    extracted_answer=_early_answer,
                )

        # ── high-priority meta-intents ────────────────────────────────────────
        if _PROFANITY.search(lower):
            # Escalate immediately if: anger present in same utterance, OR already warned before
            anger_combined = bool(_ESCALATE.search(lower))
            prior_warned = any(
                _PROFANITY.search((e.get("text") or "").lower())
                for e in (history or [])
                if e.get("event") == "caller_input"
            )
            if anger_combined or prior_warned:
                return AgentDecision(
                    intent=AgentIntent.ESCALATE,
                    confidence=0.95,
                    next_action=NextAction.ESCALATE,
                    response_text=(
                        "מצטער, אבל לא נוכל להמשיך כך. אני מעביר אותך לנציג." if he
                        else "I'm sorry, but I'll need to connect you with a member of our team now."
                    ),
                    should_save_answer=False,
                    reason="profanity+anger" if anger_combined else "profanity repeat",
                )
            # First offence — warn and continue
            restate = f" {question.prompt}" if question else ""
            return AgentDecision(
                intent=AgentIntent.CONVERSATIONAL,
                confidence=0.90,
                next_action=NextAction.CONVERSE,
                response_text=(
                    f"אבקש לשמור על שפה מכובדת — זה יעזור לנו שניהם.{restate}" if he
                    else f"I'd appreciate if we kept things respectful — it helps us both. Anyway,{restate}"
                ),
                should_save_answer=False,
            )

        if _ESCALATE.search(lower):
            wants_manager = bool(_ESCALATE_MANAGER_RE.search(lower))
            if he:
                resp = (
                    "בסדר, אני מעביר אותך למנהל עכשיו. רגע בבקשה."
                    if wants_manager
                    else "אני מעביר אותך לנציג אנושי עכשיו. רגע בבקשה."
                )
            else:
                resp = (
                    "Of course — let me connect you to a manager right now. Please hold for a moment."
                    if wants_manager
                    else "Sure — I'm connecting you to a human agent now. Please hold for a moment."
                )
            return AgentDecision(
                intent=AgentIntent.ESCALATE,
                confidence=0.90,
                next_action=NextAction.ESCALATE,
                response_text=resp,
                should_save_answer=False,
                reason="caller requested human/manager",
            )

        if _OPT_OUT.search(lower):
            return AgentDecision(
                intent=AgentIntent.OPT_OUT,
                confidence=0.92,
                next_action=NextAction.OPT_OUT,
                response_text=random.choice(_OPT_OUT_HE if he else _OPT_OUT_EN),
                should_save_answer=False,
            )

        if _NOT_NOW.search(lower):
            return AgentDecision(
                intent=AgentIntent.NOT_NOW,
                confidence=0.90,
                next_action=NextAction.RESCHEDULE,
                response_text=random.choice(_NOT_NOW_HE if he else _NOT_NOW_EN),
                should_save_answer=False,
            )

        if _CONVERSATIONAL.search(lower):
            restate = f" {question.prompt}" if question else ""
            if _NAME_RE.search(lower):
                resp_en = f"I'm Alex! Nice to meet you. Now, back to the survey —{restate}"
                resp_he = f"שמי אלכס! נעים להכיר. ממשיך —{restate}"
            elif _ARE_YOU_AI_RE.search(lower):
                resp_en = f"Ha, no — just a real person doing calls for a market research project! Anyway,{restate}"
                resp_he = f"לא, אני בן אדם אמיתי, מבצע שיחות עבור מחקר שוק! בכל מקרה,{restate}"
            elif _OPINION_RE.search(lower):
                # User asks what the agent thinks / recommends — can't take sides
                if q_type == "mcq" and options:
                    opts_str = ", ".join(options[:4])
                    resp_en = f"Ha, I really shouldn't sway you! Every option has its merits. The choices are: {opts_str} — which fits you best?"
                    resp_he = f"אני לא רוצה להשפיע! לכל אפשרות יש יתרונות. האפשרויות הן: {opts_str} — מה הכי מתאים לך?"
                else:
                    resp_en = f"I really shouldn't say — I don't want to influence your answer! So,{restate}"
                    resp_he = f"אני לא רוצה להשפיע על תשובתך! אז,{restate}"
            elif _PACE_RE.search(lower):
                # Speaking pace / volume request ("too slow" = wants faster; "too fast" = wants slower)
                slower = bool(_PACE_SLOWER_RE.search(lower)) and not bool(_PACE_FASTER_RE.search(lower))
                if slower:
                    resp_en = f"Of course, I'll slow down a bit.{restate}"
                    resp_he = f"בטח, אדבר קצת יותר לאט.{restate}"
                else:
                    resp_en = f"Sure, I'll pick up the pace.{restate}"
                    resp_he = f"בסדר, אאיץ קצת.{restate}"
            else:
                # "how are you" or generic small-talk
                resp_en = f"I'm doing well, thanks! Now,{restate}"
                resp_he = f"הכל טוב, תודה! אז,{restate}"
            return AgentDecision(
                intent=AgentIntent.CONVERSATIONAL,
                confidence=0.88,
                next_action=NextAction.CONVERSE,
                response_text=resp_he if he else resp_en,
                should_save_answer=False,
            )

        if _REPEAT.search(lower):
            return AgentDecision(
                intent=AgentIntent.REPEAT_QUESTION,
                confidence=0.88,
                next_action=NextAction.REPEAT,
                response_text="",   # FSM repeats the question
                should_save_answer=False,
            )

        if _REPHRASE.search(lower):
            # For MCQ, explain options in plain language instead of relying on FSM re-list
            rephrase_text = ""
            if q_type == "mcq" and options:
                opts_natural = ", ".join(options[:4])
                rephrase_text = (
                    f"בטח! הכוונה היא לאחת מהאפשרויות הבאות: {opts_natural}. מה הכי מתאים לך?"
                    if he else
                    f"Sure! I'm asking which of these fits best: {opts_natural}. Which one would you say?"
                )
            return AgentDecision(
                intent=AgentIntent.REPHRASE_QUESTION,
                confidence=0.85,
                next_action=NextAction.REPHRASE,
                response_text=rephrase_text,
                should_save_answer=False,
            )

        # ── answer extraction ────────────────────────────────────────────────
        extracted = self._extract(lower, text, q_type, options)
        if extracted:
            ack = self._pick_ack(extracted, q_type, he)
            response_text = self._build_transition(ack, next_question, he)
            return AgentDecision(
                intent=AgentIntent.ANSWER,
                confidence=0.82,
                next_action=NextAction.CONTINUE,
                response_text=response_text,
                should_save_answer=True,
                extracted_answer=extracted,
            )

        # free_text: any non-empty, non-noise response counts
        if q_type == "free_text" and len(text) > 2:
            ack = "תודה על תשובתך המפורטת." if he else "That's really helpful, thanks for elaborating."
            response_text = self._build_transition(ack, next_question, he)
            return AgentDecision(
                intent=AgentIntent.ANSWER,
                confidence=0.72,
                next_action=NextAction.CONTINUE,
                response_text=response_text,
                should_save_answer=True,
                extracted_answer=ExtractedAnswer(
                    value=text, type="free_text", raw_text=text
                ),
            )

        # ── unclear — context-aware clarification ────────────────────────────
        clarify = self._build_clarification(lower, q_type, options, he)
        return AgentDecision(
            intent=AgentIntent.UNCLEAR,
            confidence=0.35,
            next_action=NextAction.ASK_CLARIFICATION,
            response_text=clarify,
            should_save_answer=False,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_clarification(lower: str, q_type: str, options: list[str], he: bool) -> str:
        """Build a context-aware clarification message instead of a generic fallback."""
        # For MCQ: list options conversationally
        if q_type == "mcq" and options:
            opts_natural = " or ".join(options[:4]) if len(options) <= 4 else ", ".join(options[:3]) + f", or {options[3]}"
            if he:
                return f"לא הצלחתי לזהות את בחירתך. האפשרויות הן {opts_natural} — מה מתאים לך?"
            return f"Which would it be — {opts_natural}?"

        # For rating: remind caller of the scale
        if q_type == "rating":
            # Try to spot a partial number in the answer to confirm it
            m = re.search(r"\b(\d+)\b", lower)
            if m:
                n = m.group(1)
                if he:
                    return f"רק לוודא — התכוונת ל-{n} מתוך 10?"
                return f"Just to confirm — did you mean {n} out of 10?"
            if he:
                return "לא הבנתי. תוכל לתת לי מספר בין 1 ל-10?"
            return "I didn't catch a number. Could you give me a rating between 1 and 10?"

        # Generic fallback
        if he:
            return "לא הבנתי את תשובתך. תוכל לנסח מחדש?"
        return "I didn't quite catch that — could you rephrase your answer?"

    @staticmethod
    def _pick_ack(extracted: ExtractedAnswer, q_type: str, he: bool) -> str:
        """Choose an emotionally appropriate acknowledgement based on answer sentiment."""
        val = extracted.value
        if q_type == "rating" and isinstance(val, (int, float)):
            if val >= 7:
                return random.choice(_ACKS_HIGH_HE if he else _ACKS_HIGH_EN)
            if val <= 4:
                return random.choice(_ACKS_LOW_HE if he else _ACKS_LOW_EN)
            return random.choice(_ACKS_MID_HE if he else _ACKS_MID_EN)
        if q_type in ("yes_no", "boolean"):
            if val is True:
                return random.choice(_ACKS_HIGH_HE if he else _ACKS_HIGH_EN)
            return random.choice(_ACKS_LOW_HE if he else _ACKS_LOW_EN)
        return random.choice(_ACKS_MID_HE if he else _ACKS_MID_EN)

    @staticmethod
    def _build_transition(ack: str, next_question: QuestionContext | None, he: bool) -> str:
        """Combine acknowledgement with a brief intro to the next question.
        If there is no next question (last answer), return a warm survey closing instead."""
        if not next_question:
            # Peak-End Rule: last impression matters most
            return random.choice(_CLOSINGS_HE if he else _CLOSINGS_EN)
        _intros_en = ["Now,", "Next,", "Moving on —", "One more thing —", "Quick follow-up —"]
        _intros_he = ["עכשיו,", "בנוסף,", "שאלה נוספת —", "עוד דבר אחד —"]
        intro = random.choice(_intros_he if he else _intros_en)
        # Shorten the prompt to a natural conversational fragment (first sentence)
        prompt = next_question.prompt.split("?")[0].strip()
        if len(prompt) > 80:
            prompt = prompt[:80].rsplit(" ", 1)[0] + "..."
        return f"{ack} {intro} {prompt}?"

    def _extract(self, lower: str, raw: str, q_type: str, options: list[str] | None = None) -> ExtractedAnswer | None:
        if q_type == "rating":
            m = _DIGIT_PATTERN.search(lower)
            if m:
                return ExtractedAnswer(int(m.group(1)), "rating", raw)
            for phrase, val in _RATING_WORDS.items():
                if re.search(r'\b' + re.escape(phrase) + r'\b', lower):
                    return ExtractedAnswer(val, "rating", raw)

        elif q_type in ("yes_no", "boolean"):
            if _YES.search(lower):
                return ExtractedAnswer(True, "yes_no", raw)
            if _NO.search(lower):
                return ExtractedAnswer(False, "yes_no", raw)

        elif q_type == "mcq":
            _LETTERS = "ABCD"
            # 1. Explicit letter (A/B/C/D)
            m = _MCQ_LETTER.search(lower)
            if m:
                return ExtractedAnswer(m.group(1).upper(), "mcq", raw)
            # 2. Phonetic letter name ("bee" → B, "see" → C, "dee" → D)
            m = _MCQ_PHONETIC_RE.search(lower)
            if m:
                return ExtractedAnswer(_MCQ_PHONETIC[m.group(1).lower()], "mcq", raw)
            # 3. Ordinal word (first/second/third/fourth)
            m = _MCQ_ORDINAL_RE.search(lower)
            if m:
                letter = _MCQ_ORDINALS.get(m.group(1).lower(), "A")
                return ExtractedAnswer(letter, "mcq", raw)
            # 4. Option text match — user says the option label directly
            for i, opt in enumerate(options or []):
                if i >= 4:
                    break
                if opt.lower() in lower:
                    return ExtractedAnswer(_LETTERS[i], "mcq", raw)
            # 5. Affirmative/negative fallback — "yes/of course" → A, "no/nope" → B
            if _YES.search(lower):
                return ExtractedAnswer("A", "mcq", raw)
            if _NO.search(lower):
                return ExtractedAnswer("B", "mcq", raw)

        elif q_type == "numeric":
            m = _DIGIT_PATTERN.search(lower)
            if m:
                return ExtractedAnswer(int(m.group(1)), "numeric", raw)

        return None
