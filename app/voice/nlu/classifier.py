"""SAA-57: Baseline rule/keyword intent classifier."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@lru_cache(maxsize=512)
def _word_boundary_re(keyword: str) -> re.Pattern:
    """Compile a word-boundary regex for a keyword (cached)."""
    return re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)

from .schema import Intent, IntentType, NLUResult

# ---------------------------------------------------------------------------
# Keyword rule definitions
# ---------------------------------------------------------------------------

@dataclass
class _Rule:
    intent: IntentType
    keywords: list[str]
    base_confidence: float = 0.80


_RULES: list[_Rule] = [
    _Rule(
        IntentType.REPEAT,
        ["repeat", "say again", "say that again", "what did you say",
         "didn't hear", "didn't catch", "come again", "pardon", "what",
         "can you repeat", "once more",
         "חזור", "תחזור", "אמור שוב", "לא שמעתי", "מה אמרת", "תגיד שוב",
         "לא הצלחתי לשמוע", "שוב בבקשה"],
        base_confidence=0.85,
    ),
    _Rule(
        IntentType.REPHRASE,
        ["rephrase", "different way", "explain", "don't understand",
         "not sure what you mean", "clarify", "what do you mean",
         "can you explain", "elaborate",
         "הסבר", "תסביר", "לא הבנתי", "במילים אחרות", "מה הכוונה",
         "תנסח אחרת", "לא מובן", "פרט"],
        base_confidence=0.85,
    ),
    _Rule(
        IntentType.NOT_NOW,
        ["not now", "call back", "call me back", "later", "busy",
         "bad time", "not a good time", "another time", "maybe later",
         "i'll call back", "no thanks",
         "לא עכשיו", "תתקשר מאוחר יותר", "אחר כך", "עסוק", "עסוקה",
         "לא זמין", "לא זמינה", "מאוחר יותר", "בזמן אחר", "לא תודה"],
        base_confidence=0.88,
    ),
    _Rule(
        IntentType.SKIP,
        ["skip", "next question", "pass", "move on", "don't want to answer",
         "skip that", "next",
         "דלג", "תדלג", "שאלה הבאה", "הבא", "עבור", "לא רוצה לענות",
         "עבור הלאה"],
        base_confidence=0.82,
    ),
    _Rule(
        IntentType.HELP,
        ["help", "i'm confused", "confused", "don't know how", "lost",
         "assistance", "support",
         "עזרה", "עזור לי", "לא יודע", "לא יודעת", "מבולבל", "מבולבלת",
         "איך עונים", "לא מבין", "לא מבינה"],
        base_confidence=0.80,
    ),
    _Rule(
        IntentType.CONFIRM_YES,
        ["yes", "yeah", "yep", "correct", "that's right",
         "affirmative", "sure", "absolutely", "confirm",
         "כן", "נכון", "בדיוק", "אישור", "בסדר", "ודאי", "אכן",
         "זה נכון", "כן בדיוק", "סבבה"],
        base_confidence=0.90,
    ),
    _Rule(
        IntentType.CONFIRM_NO,
        ["no", "nope", "wrong", "incorrect", "that's wrong", "not right",
         "negative", "deny", "that's not right",
         "לא", "שגוי", "טעות", "לא נכון", "זה לא נכון", "לא זה",
         "לא מדויק", "לא בדיוק"],
        base_confidence=0.90,
    ),
]

# Rating-type answer pattern: digit 1-10 or spoken number words
_RATING_PATTERN = re.compile(r"\b(10|[1-9])\b")
_RATING_WORD_PATTERN = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|"
    r"אחד|אחת|שתיים|שניים|שני|שתי|שלוש|שלושה|ארבע|ארבעה|חמש|חמישה|"
    r"שש|שישה|שבע|שבעה|שמונה|תשע|תשעה|עשר|עשרה)\b",
    re.IGNORECASE,
)
_RATING_WORD_MAP = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "אחד": "1", "אחת": "1",
    "שתיים": "2", "שניים": "2", "שני": "2", "שתי": "2",
    "שלוש": "3", "שלושה": "3",
    "ארבע": "4", "ארבעה": "4",
    "חמש": "5", "חמישה": "5",
    "שש": "6", "שישה": "6",
    "שבע": "7", "שבעה": "7",
    "שמונה": "8",
    "תשע": "9", "תשעה": "9",
    "עשר": "10", "עשרה": "10",
}

# MCQ answer patterns (a/b/c/d or "option one" etc.)
_MCQ_LETTER_PATTERN = re.compile(r"\b([a-dA-D])\b")
_MCQ_HEBREW_LETTER_PATTERN = re.compile(r"([אבגד])")
_MCQ_ORDINAL_PATTERN = re.compile(
    r"\b(first|second|third|fourth|one|two|three|four|"
    r"ראשון|ראשונה|שני|שנייה|שלישי|שלישית|רביעי|רביעית)\b",
    re.IGNORECASE,
)

_ORDINAL_MAP = {
    "first": "1", "one": "1",
    "second": "2", "two": "2",
    "third": "3", "three": "3",
    "fourth": "4", "four": "4",
    "ראשון": "1", "ראשונה": "1",
    "שני": "2", "שנייה": "2",
    "שלישי": "3", "שלישית": "3",
    "רביעי": "4", "רביעית": "4",
}

_HEBREW_LETTER_MAP = {"א": "A", "ב": "B", "ג": "C", "ד": "D"}


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class RuleBasedClassifier:
    """Classify a transcript string into an NLUResult using keyword rules.

    question_type (optional) informs answer extraction: "rating", "mcq", "free_text".
    """

    def classify(
        self,
        text: str,
        question_type: str | None = None,
    ) -> NLUResult:
        normalised = text.lower().strip()

        # 1. Check meta intents (highest priority)
        matches: list[tuple[float, IntentType]] = []
        for rule in _RULES:
            score = self._keyword_score(normalised, rule.keywords)
            if score > 0:
                matches.append((score * rule.base_confidence, rule.intent))

        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            best_score, best_intent = matches[0]
            primary = Intent(
                intent_type=best_intent,
                confidence=min(best_score, 1.0),
                raw_text=text,
            )
            alternatives = [
                Intent(intent_type=intent, confidence=min(score, 1.0), raw_text=text)
                for score, intent in matches[1:4]
            ]
            return NLUResult(primary=primary, alternatives=alternatives)

        # 2. Try to extract an answer
        extracted = self._extract_answer(normalised, question_type)
        if extracted is not None:
            return NLUResult(
                primary=Intent(
                    intent_type=IntentType.ANSWER,
                    confidence=0.75,
                    raw_text=text,
                    extracted_value=extracted,
                )
            )

        # 3. For free_text, any non-empty utterance counts as an answer
        if question_type == "free_text" and normalised:
            return NLUResult(
                primary=Intent(
                    intent_type=IntentType.ANSWER,
                    confidence=0.70,
                    raw_text=text,
                    extracted_value=text.strip(),
                )
            )

        # 4. Unknown
        return NLUResult(
            primary=Intent(
                intent_type=IntentType.UNKNOWN,
                confidence=0.50,
                raw_text=text,
            )
        )

    # -----------------------------------------------------------------------

    def _keyword_score(self, normalised: str, keywords: list[str]) -> float:
        """Return a 0–1 score based on how strongly keywords match the text.

        Uses word-boundary matching so 'help' does not match 'helpful'.
        """
        best = 0.0
        for kw in keywords:
            if _word_boundary_re(kw).search(normalised):
                # Longer keyword match → higher weight
                weight = min(1.0, len(kw) / 10)
                score = 0.6 + 0.4 * weight
                if score > best:
                    best = score
        return best

    def _extract_answer(self, normalised: str, question_type: str | None) -> str | None:
        if question_type == "rating":
            m = _RATING_PATTERN.search(normalised)
            if m:
                return m.group(1)
            # Also accept spoken number words: "five" → "5"
            m = _RATING_WORD_PATTERN.search(normalised)
            if m:
                return _RATING_WORD_MAP[m.group(1).lower()]
            return None

        if question_type == "mcq":
            m = _MCQ_LETTER_PATTERN.search(normalised)
            if m:
                return m.group(1).upper()
            m = _MCQ_HEBREW_LETTER_PATTERN.search(normalised)
            if m:
                return _HEBREW_LETTER_MAP.get(m.group(1))
            m = _MCQ_ORDINAL_PATTERN.search(normalised)
            if m:
                return _ORDINAL_MAP.get(m.group(1).lower())

        return None
