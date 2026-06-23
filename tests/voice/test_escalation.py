"""SAA-88 + SAA-93: Tests for escalation triggers, scoring, and priority queue."""
from __future__ import annotations

import time
from datetime import datetime

import pytest

from app.voice.agent.schema import AgentDecision, AgentIntent, NextAction
from app.voice.dialogue.fsm import FSMContext, QuestionContext
from app.voice.escalation import (
    EscalationConfig,
    EscalationQueue,
    EscalationReason,
    EscalationSnapshot,
    compute_score,
    evaluate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    retry_count: int = 0,
    history: list | None = None,
    hesitation_rate: float = 0.0,
    calibrated: bool = False,
) -> FSMContext:
    ctx = FSMContext(
        session_id="s1",
        campaign_id=1,
        participant_phone="+1234567890",
        retry_count=retry_count,
    )
    ctx.history = history or []
    if calibrated:
        from app.voice.mirroring.features import VocalFeatures
        feat = VocalFeatures(
            speaking_rate_wpm=120,
            pitch_relative=0.0,
            energy_level=0.5,
            hesitation_rate=hesitation_rate,
            turn_duration_ms=3000,
        )
        ctx.mirroring_calibration.calibration_turns = 2
        ctx.mirroring_calibration.turns_observed = 2
        ctx.mirroring_calibration.baseline = feat
        ctx.mirroring_calibration.smoothed = feat
    return ctx


def _decision(intent: AgentIntent, reason: str = "") -> AgentDecision:
    return AgentDecision(
        intent=intent,
        confidence=0.9,
        next_action=NextAction.ESCALATE,
        response_text="",
        should_save_answer=False,
        reason=reason,
    )


def _unclear_history(n: int) -> list[dict]:
    history = []
    for _ in range(n):
        history.append({"event": "caller_input", "agent_intent": AgentIntent.UNCLEAR.value})
        history.append({"event": "bot_response", "text": "Could you repeat?"})
    return history


# ---------------------------------------------------------------------------
# SAA-85: Escalation Rules
# ---------------------------------------------------------------------------

class TestEscalationRules:
    def test_agent_escalate_maps_to_agent_requested(self):
        ctx = _ctx()
        d = _decision(AgentIntent.ESCALATE, reason="caller wants a human agent")
        assert evaluate(ctx, d, rapport=0.8) == EscalationReason.AGENT_REQUESTED

    def test_profanity_reason(self):
        ctx = _ctx()
        d = _decision(AgentIntent.ESCALATE, reason="profanity detected")
        assert evaluate(ctx, d, rapport=0.8) == EscalationReason.PROFANITY

    def test_angry_caller_reason(self):
        ctx = _ctx()
        d = _decision(AgentIntent.ESCALATE, reason="caller is angry and furious")
        assert evaluate(ctx, d, rapport=0.8) == EscalationReason.ANGRY_CALLER

    def test_max_retries_fires(self):
        cfg = EscalationConfig(max_retries=3)
        ctx = _ctx(retry_count=3)
        assert evaluate(ctx, None, rapport=0.8, config=cfg) == EscalationReason.MAX_RETRIES

    def test_max_retries_not_yet(self):
        cfg = EscalationConfig(max_retries=4)
        ctx = _ctx(retry_count=2)
        assert evaluate(ctx, None, rapport=0.8, config=cfg) is None

    def test_repeated_unclear_fires(self):
        cfg = EscalationConfig(max_unclear_streak=3)
        ctx = _ctx(history=_unclear_history(3))
        assert evaluate(ctx, None, rapport=0.8, config=cfg) == EscalationReason.REPEATED_UNCLEAR

    def test_repeated_unclear_not_yet(self):
        cfg = EscalationConfig(max_unclear_streak=3)
        ctx = _ctx(history=_unclear_history(2))
        assert evaluate(ctx, None, rapport=0.8, config=cfg) is None

    def test_high_distress_fires(self):
        cfg = EscalationConfig(high_hesitation_threshold=0.4)
        ctx = _ctx(hesitation_rate=0.45, calibrated=True)
        assert evaluate(ctx, None, rapport=0.8, config=cfg) == EscalationReason.HIGH_DISTRESS

    def test_high_distress_not_yet(self):
        cfg = EscalationConfig(high_hesitation_threshold=0.4)
        ctx = _ctx(hesitation_rate=0.3, calibrated=True)
        assert evaluate(ctx, None, rapport=0.8, config=cfg) is None

    def test_low_rapport_fires(self):
        cfg = EscalationConfig(low_rapport_threshold=0.45)
        ctx = _ctx(calibrated=True)
        assert evaluate(ctx, None, rapport=0.40, config=cfg) == EscalationReason.LOW_RAPPORT

    def test_low_rapport_not_yet(self):
        cfg = EscalationConfig(low_rapport_threshold=0.45)
        ctx = _ctx(calibrated=True)
        assert evaluate(ctx, None, rapport=0.50, config=cfg) is None

    def test_no_trigger_returns_none(self):
        ctx = _ctx()
        assert evaluate(ctx, None, rapport=0.9) is None

    def test_agent_escalate_takes_priority_over_max_retries(self):
        ctx = _ctx(retry_count=10)
        d = _decision(AgentIntent.ESCALATE, reason="profanity detected")
        # Should return PROFANITY (rule 1), not MAX_RETRIES (rule 2)
        assert evaluate(ctx, d, rapport=0.8) == EscalationReason.PROFANITY

    def test_uncalibrated_skips_psycho_rules(self):
        # High hesitation but not calibrated → no HIGH_DISTRESS
        ctx = _ctx(hesitation_rate=0.9, calibrated=False)
        assert evaluate(ctx, None, rapport=0.3) is None


# ---------------------------------------------------------------------------
# SAA-91: Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_profanity_highest_base(self):
        s = compute_score(EscalationReason.PROFANITY, rapport=0.9)
        assert s >= 10.0

    def test_low_rapport_raises_score(self):
        s_low  = compute_score(EscalationReason.REPEATED_UNCLEAR, rapport=0.3)
        s_high = compute_score(EscalationReason.REPEATED_UNCLEAR, rapport=0.9)
        assert s_low > s_high

    def test_high_hesitation_raises_score(self):
        s_high = compute_score(EscalationReason.HIGH_DISTRESS, hesitation_rate=0.6)
        s_low  = compute_score(EscalationReason.HIGH_DISTRESS, hesitation_rate=0.1)
        assert s_high > s_low

    def test_near_complete_lowers_score(self):
        s_start = compute_score(EscalationReason.MAX_RETRIES, answers_completed=0, total_questions=10)
        s_end   = compute_score(EscalationReason.MAX_RETRIES, answers_completed=9, total_questions=10)
        assert s_start > s_end

    def test_score_is_positive(self):
        for reason in EscalationReason:
            assert compute_score(reason) > 0


# ---------------------------------------------------------------------------
# SAA-90 + SAA-92: Priority Queue
# ---------------------------------------------------------------------------

def _snap(session_id: str, score: float, ts: datetime | None = None) -> EscalationSnapshot:
    s = EscalationSnapshot(
        session_id=session_id,
        campaign_id=1,
        participant_phone="+1",
        reason=EscalationReason.MAX_RETRIES,
        triggered_at=ts or datetime.now(),
        urgency_score=score,
    )
    return s


class TestEscalationQueue:
    def test_pop_returns_highest_score(self):
        q = EscalationQueue()
        q.push(_snap("a", 5.0))
        q.push(_snap("b", 9.0))
        q.push(_snap("c", 3.0))
        assert q.pop().session_id == "b"

    def test_fifo_tie_breaking(self):
        q = EscalationQueue()
        t1 = datetime(2026, 1, 1, 10, 0, 0)
        t2 = datetime(2026, 1, 1, 10, 0, 1)
        q.push(_snap("early",  7.0, t1))
        q.push(_snap("late",   7.0, t2))
        assert q.pop().session_id == "early"

    def test_remove_session(self):
        q = EscalationQueue()
        q.push(_snap("x", 8.0))
        assert q.remove("x") is True
        assert q.pop() is None

    def test_remove_nonexistent(self):
        q = EscalationQueue()
        assert q.remove("nope") is False

    def test_all_sorted_order(self):
        q = EscalationQueue()
        q.push(_snap("low",  2.0))
        q.push(_snap("high", 9.0))
        q.push(_snap("mid",  5.0))
        ids = [s.session_id for s in q.all_sorted()]
        assert ids == ["high", "mid", "low"]

    def test_len(self):
        q = EscalationQueue()
        q.push(_snap("a", 1.0))
        q.push(_snap("b", 2.0))
        assert len(q) == 2

    def test_replace_existing_session(self):
        q = EscalationQueue()
        q.push(_snap("a", 3.0))
        q.push(_snap("a", 9.0))  # re-push same session with higher score
        assert len(q) == 1
        assert q.pop().urgency_score == 9.0

    def test_pop_empty_returns_none(self):
        q = EscalationQueue()
        assert q.pop() is None

    def test_peek_does_not_remove(self):
        q = EscalationQueue()
        q.push(_snap("x", 5.0))
        q.peek()
        assert len(q) == 1
