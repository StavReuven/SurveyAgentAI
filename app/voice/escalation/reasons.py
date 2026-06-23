"""SAA-86: Escalation reason taxonomy."""
from __future__ import annotations
from enum import Enum


class EscalationReason(str, Enum):
    AGENT_REQUESTED  = "agent_requested"   # caller explicitly asked for a human
    PROFANITY        = "profanity"         # abusive language detected
    REPEATED_UNCLEAR = "repeated_unclear"  # too many consecutive unclear responses
    HIGH_DISTRESS    = "high_distress"     # psycho-adaptive: high hesitation rate
    LOW_RAPPORT      = "low_rapport"       # psycho-adaptive: consistently low STT confidence
    MAX_RETRIES      = "max_retries"       # FSM fallback counter exhausted
    ANGRY_CALLER     = "angry_caller"      # strong negative sentiment
