"""Regression test: the pipeline's response-text merge must not speak a
question from the naive linear peek when a branch rule actually redirected
the FSM elsewhere (see app/voice/pipeline.py process_turn)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.voice.agent.service import AgentAIService
from app.voice.dialogue.fsm import QuestionContext
from app.voice.pipeline import VoicePipeline
from app.voice.stt.adapter import MockSTTAdapter


async def _audio_chunks():
    yield b"x"


@pytest.mark.asyncio
async def test_pipeline_speaks_branched_question_not_linear_peek():
    q1 = QuestionContext(question_id=1, question_key="q1", prompt="messi or ronaldo?",
                          question_type="mcq", order_index=0, config={"options": ["messi", "ronaldo"]})
    q2 = QuestionContext(question_id=2, question_key="q2", prompt="argentina or spain?",
                          question_type="mcq", order_index=1, config={"options": ["argentina", "spain"]})
    q3 = QuestionContext(question_id=3, question_key="q3", prompt="BIBI or tibi?",
                          question_type="mcq", order_index=2, config={"options": ["BIBI", "tibi"]})

    rules = [
        SimpleNamespace(source_question_id=1, target_question_id=2, operator="equals",
                         value="messi", action="goto", priority=100),
        SimpleNamespace(source_question_id=1, target_question_id=3, operator="equals",
                         value="ronaldo", action="goto", priority=100),
    ]

    pipeline = VoicePipeline(
        stt_adapter=MockSTTAdapter(responses=["Ronaldo"], confidence=0.95),
        agent_service=AgentAIService(),  # no ANTHROPIC_API_KEY in test env -> rule-based fallback
    )
    ctx = pipeline.create_session(
        campaign_id=1, participant_phone="+1", questions=[q1, q2, q3], branch_rules=rules,
    )
    await pipeline.start_session(ctx)

    result = await pipeline.process_turn(ctx, _audio_chunks())

    assert ctx.current_question is q3, "branch rule should skip straight to q3"
    lowered = result.response_text.lower()
    assert "bibi" in lowered or "tibi" in lowered
    assert "argentina" not in lowered and "spain" not in lowered, (
        f"spoke about the linearly-peeked q2 instead of the branched q3: {result.response_text!r}"
    )
