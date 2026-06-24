"""AgentAI structured output schema — intent, decision, extracted answer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentIntent(str, Enum):
    ANSWER            = "ANSWER"
    REPEAT_QUESTION   = "REPEAT_QUESTION"
    REPHRASE_QUESTION = "REPHRASE_QUESTION"
    NOT_NOW           = "NOT_NOW"
    OPT_OUT           = "OPT_OUT"
    UNCLEAR           = "UNCLEAR"
    OFF_TOPIC         = "OFF_TOPIC"
    ESCALATE          = "ESCALATE"
    CONVERSATIONAL    = "CONVERSATIONAL"   # meta/small-talk the agent answers briefly


class NextAction(str, Enum):
    CONTINUE          = "CONTINUE"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    REPEAT            = "REPEAT"
    REPHRASE          = "REPHRASE"
    RESCHEDULE        = "RESCHEDULE"
    OPT_OUT           = "OPT_OUT"
    ESCALATE          = "ESCALATE"
    END_SURVEY        = "END_SURVEY"
    CONVERSE          = "CONVERSE"         # answer briefly, then return to survey


@dataclass
class ExtractedAnswer:
    value: Any       # int for rating, bool for yes_no, str for mcq/free_text/numeric
    type: str        # "rating" | "mcq" | "free_text" | "yes_no" | "numeric"
    raw_text: str    # the respondent's original words

    def to_dict(self) -> dict:
        return {"value": self.value, "type": self.type, "raw_text": self.raw_text}

    def as_fsm_value(self) -> str:
        """Normalise to string for FSM answer storage."""
        if isinstance(self.value, bool):
            return "yes" if self.value else "no"
        return str(self.value)


@dataclass
class AgentDecision:
    intent: AgentIntent
    confidence: float
    next_action: NextAction
    response_text: str
    should_save_answer: bool
    extracted_answer: ExtractedAnswer | None = None
    next_question_id: str | None = None
    reason: str = ""   # internal reasoning, never spoken

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "extracted_answer": self.extracted_answer.to_dict() if self.extracted_answer else None,
            "confidence": round(self.confidence, 4),
            "next_action": self.next_action.value,
            "response_text": self.response_text,
            "should_save_answer": self.should_save_answer,
            "next_question_id": self.next_question_id,
        }

    def to_nlu_intent(self):
        """Convert to the existing NLU Intent so the FSM processes it unchanged."""
        from app.voice.nlu.schema import Intent, IntentType

        # Map agent intent → FSM intent type
        _MAP = {
            AgentIntent.ANSWER:            IntentType.ANSWER,
            AgentIntent.REPEAT_QUESTION:   IntentType.REPEAT,
            AgentIntent.REPHRASE_QUESTION: IntentType.REPHRASE,
            AgentIntent.NOT_NOW:           IntentType.NOT_NOW,
            AgentIntent.OPT_OUT:           IntentType.NOT_NOW,
            AgentIntent.UNCLEAR:           IntentType.UNKNOWN,
            AgentIntent.OFF_TOPIC:         IntentType.UNKNOWN,
            AgentIntent.ESCALATE:          IntentType.UNKNOWN,
            AgentIntent.CONVERSATIONAL:    IntentType.UNKNOWN,  # no FSM advance
        }
        intent_type = _MAP.get(self.intent, IntentType.UNKNOWN)

        # If the agent recognises an answer but wants clarification, downgrade to UNKNOWN
        if self.intent == AgentIntent.ANSWER and not self.should_save_answer:
            intent_type = IntentType.UNKNOWN

        # Override FSM confidence so it follows the agent's next_action decision:
        #   CONTINUE  → force auto-accept (≥ 0.90 threshold)
        #   otherwise → use raw confidence (triggers confirm / fallback naturally)
        if self.next_action == NextAction.CONTINUE and self.should_save_answer:
            fsm_confidence = 0.95
        elif self.next_action == NextAction.ASK_CLARIFICATION:
            fsm_confidence = 0.40   # below low_confidence → FSM fallback
        else:
            fsm_confidence = self.confidence

        extracted_value = (
            self.extracted_answer.as_fsm_value()
            if self.extracted_answer and self.should_save_answer
            else None
        )

        return Intent(
            intent_type=intent_type,
            confidence=fsm_confidence,
            raw_text="",
            extracted_value=extracted_value,
        )
