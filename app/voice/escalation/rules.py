"""SAA-85: Escalation trigger rules.

Each rule inspects the current pipeline state and returns an EscalationReason
if the rule fires, or None if it does not.  Rules are evaluated in priority
order; the first match wins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..agent.schema import AgentDecision, AgentIntent
from ..dialogue.fsm import FSMContext
from .reasons import EscalationReason

if TYPE_CHECKING:
    pass


@dataclass
class EscalationConfig:
    max_unclear_streak: int = 3       # consecutive UNCLEAR turns before escalation
    high_hesitation_threshold: float = 0.40   # hesitation_rate above this → distress
    low_rapport_threshold: float = 0.45       # avg STT confidence below this → low rapport
    max_retries: int = 4              # FSM retry_count at or above this → escalate


def evaluate(
    ctx: FSMContext,
    agent_decision: AgentDecision | None,
    rapport: float,
    config: EscalationConfig | None = None,
) -> EscalationReason | None:
    """Return the first matching EscalationReason, or None."""
    cfg = config or EscalationConfig()

    # Rule 1 — agent explicitly flagged ESCALATE (profanity, anger, human request)
    if agent_decision and agent_decision.intent == AgentIntent.ESCALATE:
        reason_text = (agent_decision.reason or "").lower()
        if any(w in reason_text for w in ("profan", "swear", "abuse", "fuck", "shit")):
            return EscalationReason.PROFANITY
        if any(w in reason_text for w in ("anger", "angry", "furious", "upset")):
            return EscalationReason.ANGRY_CALLER
        return EscalationReason.AGENT_REQUESTED

    # Rule 2 — FSM retry counter exhausted (stuck on one question)
    if ctx.retry_count >= cfg.max_retries:
        return EscalationReason.MAX_RETRIES

    # Rule 3 — too many consecutive UNCLEAR turns in history
    unclear_streak = _count_unclear_streak(ctx)
    if unclear_streak >= cfg.max_unclear_streak:
        return EscalationReason.REPEATED_UNCLEAR

    # Rule 4 — psycho-adaptive: high hesitation rate (caller distressed)
    cal = ctx.mirroring_calibration
    if cal.is_calibrated and cal.smoothed is not None:
        if cal.smoothed.hesitation_rate >= cfg.high_hesitation_threshold:
            return EscalationReason.HIGH_DISTRESS

    # Rule 5 — psycho-adaptive: persistently low STT rapport (caller struggling)
    if cal.is_calibrated and rapport < cfg.low_rapport_threshold:
        return EscalationReason.LOW_RAPPORT

    return None


def _count_unclear_streak(ctx: FSMContext) -> int:
    """Count consecutive UNCLEAR agent intents at the tail of history."""
    streak = 0
    for entry in reversed(ctx.history):
        if entry.get("event") == "bot_response":
            continue
        if entry.get("event") == "caller_input":
            agent_intent = entry.get("agent_intent")
            if agent_intent == AgentIntent.UNCLEAR.value:
                streak += 1
            else:
                break
    return streak
