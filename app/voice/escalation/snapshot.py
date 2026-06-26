"""SAA-87: EscalationSnapshot — captures full session context at trigger time."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .reasons import EscalationReason


@dataclass
class EscalationSnapshot:
    session_id: str
    campaign_id: int
    participant_phone: str
    reason: EscalationReason
    triggered_at: datetime = field(default_factory=datetime.now)

    # Conversation state
    current_question_key: str | None = None
    current_question_prompt: str | None = None
    answers_so_far: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    # Psycho-adaptive metrics at trigger time
    mirroring_snapshot: dict = field(default_factory=dict)
    rapport_score: float = 0.0

    # Scoring (filled by priority queue service)
    urgency_score: float = 0.0

    # Operator state
    operator_id: str | None = None        # set when an operator takes over
    taken_over_at: datetime | None = None
    returned_at: datetime | None = None   # set when returned to agent

    def to_dict(self) -> dict:
        return {
            "session_id":            self.session_id,
            "campaign_id":           self.campaign_id,
            "participant_phone":     self.participant_phone,
            "reason":                self.reason.value,
            "triggered_at":          self.triggered_at.isoformat(),
            "current_question_key":  self.current_question_key,
            "current_question_prompt": self.current_question_prompt,
            "answers_so_far":        self.answers_so_far,
            "history":               self.history,
            "mirroring_snapshot":    self.mirroring_snapshot,
            "rapport_score":         self.rapport_score,
            "urgency_score":         round(self.urgency_score, 4),
            "operator_id":           self.operator_id,
            "taken_over_at":         self.taken_over_at.isoformat() if self.taken_over_at else None,
            "returned_at":           self.returned_at.isoformat() if self.returned_at else None,
        }
