"""Rule-based fallback agent — used when the Claude API is unavailable."""

from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

from .schema import AgentDecision, AgentIntent, ExtractedAnswer, NextAction

if TYPE_CHECKING:
    from app.voice.dialogue.fsm import QuestionContext

# ── varied acknowledgements ───────────────────────────────────────────────────
_ACKS_EN = [
    "Got it.", "Thanks for that.", "Noted.", "Appreciate it.",
    "Understood.", "Great, thank you.", "Perfect.",
]
_ACKS_HE = [
    "תודה.", "מצוין.", "הבנתי.", "טוב מאוד.", "מעולה.", "יפה.",
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
    r"|come again|pardon|what\??|could you repeat|once more)\b",
    re.I,
)
_REPHRASE = re.compile(
    r"\b(rephrase|different(ly)?|explain|don'?t understand|not sure what you mean"
    r"|clarify|what do you mean|can you explain|elaborate|i'?m confused)\b",
    re.I,
)
_ESCALATE = re.compile(
    r"\b("
    r"speak to (a )?(human|person|agent|representative)|transfer|manager"
    r"|i'?m (angry|furious|livid)|this is (ridiculous|outrageous|unacceptable)"
    r"|stop wasting my time|waste of (my )?time|this is pointless"
    r"|you'?re (useless|terrible|awful|a robot)"
    r"|i hate this|terrible (service|experience)"
    r")\b",
    re.I,
)
_PROFANITY = re.compile(
    r"\b(fuck|shit|bitch|asshole|bastard|damn it|crap|dick|pussy|motherfucker"
    r"|wtf|go to hell|screw you|idiot|moron|stupid (bot|survey|call)"
    r"|כסח|לך תזדיין|בן זונה|מה הבאסה|זיין|שמות גידוף)\b",
    re.I,
)

# ── answer extraction patterns ────────────────────────────────────────────────

# Digit pattern — skip time-like formats (8:20, 12:30)
_TIME_PATTERN  = re.compile(r"\b\d{1,2}:\d{2}\b")
_DIGIT_PATTERN = re.compile(r"(?<!\d)(?<!\d:)\b(10|[1-9])\b(?!:\d)")

_RATING_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    # Hebrew number words
    "אחד": 1, "אחת": 1,
    "שתיים": 2, "שניים": 2, "שני": 2,
    "שלוש": 3, "שלושה": 3,
    "ארבע": 4, "ארבעה": 4,
    "חמש": 5, "חמישה": 5,
    "שש": 6, "שישה": 6,
    "שבע": 7, "שבעה": 7,
    "שמונה": 8,
    "תשע": 9, "תשעה": 9,
    "עשר": 10, "עשרה": 10,
    # Hebrew sentiment → approximate rating
    "גרוע": 2, "גרועה": 2, "נורא": 1, "איום": 1,
    "ממוצע": 5, "בסדר": 5, "סביר": 5,
    "טוב": 7, "טובה": 7, "נחמד": 7, "נחמדה": 7,
    "מצוין": 9, "מצוינת": 9, "מעולה": 10, "נהדר": 9, "נהדרת": 9,
    # English sentiment → approximate rating
    "terrible": 1, "awful": 1, "horrible": 1,
    "bad": 2, "poor": 2, "disappointing": 2,
    "okay": 5, "ok": 5, "average": 5, "fine": 5, "alright": 5, "mediocre": 5,
    "good": 7, "nice": 7, "well": 7, "great": 8, "pretty good": 7,
    "excellent": 9, "perfect": 10, "amazing": 9, "wonderful": 9,
    "outstanding": 10, "fantastic": 9, "superb": 10,
}
_YES = re.compile(
    r"\b(yes|yeah|yep|yup|sure|absolutely|correct|that'?s right|affirmative"
    r"|of course|definitely|certainly|indeed"
    r"|כן|בהחלט|נכון|ודאי|אכן|בטח|סבבה|ממליץ|ממליצה)\b",
    re.I,
)
_NO = re.compile(
    r"\b(no|nope|nah|not really|negative|incorrect|wrong|never|not at all"
    r"|לא|שגוי|לא ממליץ|לא ממליצה)\b",
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
    ) -> AgentDecision:
        text = transcript.strip()
        lower = text.lower()
        q_type = question.question_type if question else "free_text"
        he = language.startswith("he")

        # ── high-priority meta-intents ────────────────────────────────────────
        if _PROFANITY.search(lower):
            return AgentDecision(
                intent=AgentIntent.ESCALATE,
                confidence=0.95,
                next_action=NextAction.ESCALATE,
                response_text=(
                    "אנא שמור על שפה מכובדת. אני מעביר אותך לנציג אנושי." if he
                    else "Please keep the conversation respectful. I'm transferring you to a human agent now."
                ),
                should_save_answer=False,
                reason="profanity detected",
            )

        if _ESCALATE.search(lower):
            return AgentDecision(
                intent=AgentIntent.ESCALATE,
                confidence=0.90,
                next_action=NextAction.ESCALATE,
                response_text=(
                    "אני מעביר אותך לנציג אנושי. רגע בבקשה." if he
                    else "I'm transferring you to a human agent now. Please hold."
                ),
                should_save_answer=False,
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

        if _REPEAT.search(lower):
            return AgentDecision(
                intent=AgentIntent.REPEAT_QUESTION,
                confidence=0.88,
                next_action=NextAction.REPEAT,
                response_text="",   # FSM repeats the question
                should_save_answer=False,
            )

        if _REPHRASE.search(lower):
            return AgentDecision(
                intent=AgentIntent.REPHRASE_QUESTION,
                confidence=0.85,
                next_action=NextAction.REPHRASE,
                response_text="",   # FSM rephrases
                should_save_answer=False,
            )

        # ── answer extraction ────────────────────────────────────────────────
        options: list[str] = (question.config or {}).get("options", []) if question else []
        extracted = self._extract(lower, text, q_type, options)
        if extracted:
            ack = random.choice(_ACKS_HE if he else _ACKS_EN)
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
            ack = "תודה על תשובתך." if he else "Thanks for sharing that."
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
        # For MCQ: remind caller of valid options
        if q_type == "mcq" and options:
            opts_str = ", ".join(
                f"{chr(65+i)}) {opt}" for i, opt in enumerate(options[:4])
            )
            if he:
                return f"לא הצלחתי לזהות את הבחירה שלך. האפשרויות הן: {opts_str}. איזו מהן?"
            return f"I didn't catch your choice. The options are: {opts_str}. Which one do you mean?"

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
    def _build_transition(ack: str, next_question: QuestionContext | None, he: bool) -> str:
        """Combine acknowledgement with a brief intro to the next question.
        If there is no next question (last answer), return a warm survey closing instead."""
        if not next_question:
            # Peak-End Rule: last impression matters most
            return random.choice(_CLOSINGS_HE if he else _CLOSINGS_EN)
        _intros_en = ["Now,", "Next,", "Moving on —", "One more thing —", "And"]
        _intros_he = ["עכשיו,", "בנוסף,", "שאלה נוספת —", "ו-"]
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
