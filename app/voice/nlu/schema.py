"""SAA-56: Intent schema — types and result structures for NLU."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IntentType(str, Enum):
    """All intents the Voice AI Pipeline recognises during a survey call."""

    # Meta / navigation
    REPEAT = "repeat"          # caller wants the question repeated
    REPHRASE = "rephrase"      # caller wants it said differently
    NOT_NOW = "not_now"        # caller wants to defer / call back later
    SKIP = "skip"              # caller wants to skip the current question
    HELP = "help"              # caller is confused and needs assistance

    # Answer-bearing intents (one per question type)
    ANSWER = "answer"          # caller is providing an answer

    # Confirmation intents (used in confirmation sub-state)
    CONFIRM_YES = "confirm_yes"
    CONFIRM_NO = "confirm_no"

    # Fallback
    UNKNOWN = "unknown"        # could not determine intent


@dataclass
class Intent:
    """Classified intent with associated metadata."""

    intent_type: IntentType
    confidence: float                    # 0.0–1.0
    raw_text: str                        # original transcript text
    extracted_value: str | None = None   # for ANSWER intents: the parsed value

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.60

    @property
    def is_answer(self) -> bool:
        return self.intent_type == IntentType.ANSWER


@dataclass
class NLUResult:
    """Full result from a single NLU pass."""

    primary: Intent
    alternatives: list[Intent] = field(default_factory=list)
    language: str = "en"

    @property
    def best(self) -> Intent:
        return self.primary
