"""SAA-91: Urgency scoring for escalated sessions."""
from __future__ import annotations

from .reasons import EscalationReason

# Base urgency weight per reason (higher = more urgent)
_REASON_WEIGHTS: dict[EscalationReason, float] = {
    EscalationReason.PROFANITY:        10.0,
    EscalationReason.ANGRY_CALLER:      9.0,
    EscalationReason.HIGH_DISTRESS:     8.0,
    EscalationReason.AGENT_REQUESTED:   7.0,
    EscalationReason.REPEATED_UNCLEAR:  5.0,
    EscalationReason.MAX_RETRIES:       4.0,
    EscalationReason.LOW_RAPPORT:       3.0,
}


def compute_score(
    reason: EscalationReason,
    rapport: float = 0.8,
    hesitation_rate: float = 0.0,
    answers_completed: int = 0,
    total_questions: int = 1,
) -> float:
    """Return a float urgency score (higher = needs attention sooner).

    Components:
    - base: fixed weight per reason
    - rapport bonus: low confidence → higher urgency (max +5)
    - hesitation bonus: distressed speech → higher urgency (max +5)
    - progress penalty: calls near completion are less critical (max -2)
    """
    base = _REASON_WEIGHTS.get(reason, 5.0)

    # Low rapport bonus: if rapport < 0.5 add up to +5
    rapport_bonus = max(0.0, (0.5 - rapport) * 10.0)

    # High hesitation bonus: if hesitation_rate > 0.25 add up to +5
    hesitation_bonus = max(0.0, (hesitation_rate - 0.25) * 20.0)

    # Progress penalty: nearly complete calls are less urgent
    progress = answers_completed / max(total_questions, 1)
    progress_penalty = progress * 2.0

    return round(base + rapport_bonus + hesitation_bonus - progress_penalty, 4)
