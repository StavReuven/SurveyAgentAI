"""SAA-60: FSM specification — states, actions, and session context."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models import BranchRule as BranchRuleModel

from app.voice.mirroring.calibration import SessionCalibration


class DialogueState(str, Enum):
    """All states the dialogue FSM can be in."""

    GREETING = "greeting"          # initial greeting, consent check
    ASKING = "asking"              # speaking the current question
    WAITING = "waiting"            # waiting for caller's response
    CONFIRMING = "confirming"      # repeating answer back for confirmation
    REPEATING = "repeating"        # repeating the question verbatim
    REPHRASING = "rephrasing"      # rephrasing the question
    FALLBACK = "fallback"          # handling repeated failures / unknown responses
    CLOSING = "closing"            # wrapping up the call
    DONE = "done"                  # call ended normally
    ERROR = "error"                # unrecoverable error


class DialogueAction(str, Enum):
    """Actions the pipeline should execute after a state transition."""

    SPEAK_GREETING = "speak_greeting"
    SPEAK_QUESTION = "speak_question"
    SPEAK_REPHRASE = "speak_rephrase"
    CONFIRM_ANSWER = "confirm_answer"
    ACCEPT_ANSWER = "accept_answer"      # store answer and advance
    REPEAT_QUESTION = "repeat_question"
    SPEAK_FALLBACK = "speak_fallback"
    SPEAK_CLOSING = "speak_closing"
    END_CALL = "end_call"
    ESCALATE = "escalate"


@dataclass
class QuestionContext:
    """Snapshot of the current question being asked."""

    question_id: int
    question_key: str
    prompt: str
    question_type: str           # "rating" | "mcq" | "free_text"
    order_index: int
    config: dict = field(default_factory=dict)


@dataclass
class FSMContext:
    """Mutable state for one ongoing survey call session."""

    session_id: str
    campaign_id: int
    participant_phone: str

    state: DialogueState = DialogueState.GREETING
    current_question_index: int = 0       # index into ordered questions list
    retry_count: int = 0                  # consecutive failed turns on this question
    pending_answer: str | None = None     # answer awaiting confirmation

    answers: dict[str, str] = field(default_factory=dict)   # key → value
    history: list[dict[str, Any]] = field(default_factory=list)

    # Populated by the pipeline from the DB
    questions: list[QuestionContext] = field(default_factory=list)
    branch_rules: list[Any] = field(default_factory=list)  # list[BranchRuleModel]

    # SAA-72: per-session vocal calibration for voice mirroring
    mirroring_calibration: SessionCalibration = field(default_factory=SessionCalibration)

    @property
    def current_question(self) -> QuestionContext | None:
        if 0 <= self.current_question_index < len(self.questions):
            return self.questions[self.current_question_index]
        return None

    @property
    def is_last_question(self) -> bool:
        return self.current_question_index >= len(self.questions) - 1

    def advance_question(self, target_index: int | None = None) -> None:
        self.retry_count = 0
        self.pending_answer = None
        if target_index is not None:
            self.current_question_index = target_index
        else:
            self.current_question_index += 1

    def _branch_rules_for_current(self, question_id: int) -> list[Any]:
        return [r for r in self.branch_rules if r.source_question_id == question_id]

    def log(self, event: str, **kwargs: Any) -> None:
        self.history.append({"event": event, **kwargs})
