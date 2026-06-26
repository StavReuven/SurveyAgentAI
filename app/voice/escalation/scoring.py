"""SAA-91: Urgency scoring for escalated sessions."""
from __future__ import annotations

from .reasons import EscalationReason

# Base urgency weight per reason (higher = more urgent)
_REASON_WEIGHTS: dict[EscalationReason, float] = {
    EscalationReason.HIGH_DISTRESS:     9.0,   # psychological distress = highest care priority
    EscalationReason.ANGRY_CALLER:      8.7,   # active hostility — needs human de-escalation
    EscalationReason.PROFANITY:         8.5,   # only escalated on repeat — still serious
    EscalationReason.AGENT_REQUESTED:   8.0,   # explicit request deserves prompt response
    EscalationReason.REPEATED_UNCLEAR:  5.0,
    EscalationReason.MAX_RETRIES:       4.0,
    EscalationReason.LOW_RAPPORT:       2.0,   # softest signal, shouldn't jump the queue
}


def compute_score(
    reason: EscalationReason,
    rapport: float = 0.8,
    hesitation_rate: float = 0.0,
    answers_completed: int = 0,
    total_questions: int = 1,
    prior_escalations: int = 0,
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

    # Repeat-escalation bonus: each prior escalation in this session adds +2 (max +6)
    repeat_bonus = min(prior_escalations * 2.0, 6.0)

    return round(base + rapport_bonus + hesitation_bonus - progress_penalty + repeat_bonus, 4)
