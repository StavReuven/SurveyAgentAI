"""SAA-63: Unit tests for Dialogue Manager (FSM + transitions + fallbacks)."""

from __future__ import annotations

import pytest

from app.voice.dialogue.fallbacks import FallbackConfig, FallbackHandler
from app.voice.dialogue.fsm import (
    DialogueAction,
    DialogueState,
    FSMContext,
    QuestionContext,
)
from app.voice.dialogue.transitions import DialogueManager
from app.voice.nlu.schema import Intent, IntentType


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_question(
    idx: int = 0,
    question_type: str = "rating",
    prompt: str | None = None,
) -> QuestionContext:
    return QuestionContext(
        question_id=idx + 1,
        question_key=f"q{idx + 1}",
        prompt=prompt or f"Question {idx + 1}: please rate us from 1 to 10.",
        question_type=question_type,
        order_index=idx,
    )


def _make_ctx(num_questions: int = 2, question_type: str = "rating") -> FSMContext:
    ctx = FSMContext(
        session_id="test-session",
        campaign_id=1,
        participant_phone="+1234567890",
        questions=[_make_question(i, question_type) for i in range(num_questions)],
        branch_rules=[],
    )
    return ctx


def _intent(intent_type: IntentType, confidence: float = 0.92, value: str | None = None) -> Intent:
    return Intent(
        intent_type=intent_type,
        confidence=confidence,
        raw_text="test utterance",
        extracted_value=value,
    )


# ---------------------------------------------------------------------------
# FSMContext tests
# ---------------------------------------------------------------------------

class TestFSMContext:
    def test_current_question_first(self):
        ctx = _make_ctx(2)
        q = ctx.current_question
        assert q is not None
        assert q.question_id == 1

    def test_advance_question(self):
        ctx = _make_ctx(2)
        ctx.advance_question()
        assert ctx.current_question_index == 1

    def test_is_last_question(self):
        ctx = _make_ctx(1)
        assert ctx.is_last_question is True

    def test_no_current_question_when_exhausted(self):
        ctx = _make_ctx(1)
        ctx.advance_question()
        assert ctx.current_question is None

    def test_log_appends_to_history(self):
        ctx = _make_ctx()
        ctx.log("test_event", foo="bar")
        assert ctx.history[-1] == {"event": "test_event", "foo": "bar"}


# ---------------------------------------------------------------------------
# DialogueManager — session start
# ---------------------------------------------------------------------------

class TestDialogueManagerStart:
    def test_start_returns_greeting_action(self):
        dm = DialogueManager()
        ctx = _make_ctx()
        ctx, action, text = dm.start(ctx)
        assert action == DialogueAction.SPEAK_GREETING
        assert ctx.state == DialogueState.ASKING
        assert len(text) > 0

    def test_start_includes_first_question(self):
        dm = DialogueManager()
        ctx = _make_ctx()
        ctx, action, text = dm.start(ctx)
        assert "Question 1" in text


# ---------------------------------------------------------------------------
# DialogueManager — REPEAT intent
# ---------------------------------------------------------------------------

class TestRepeatIntent:
    def test_repeat_in_waiting_stays_on_question(self):
        dm = DialogueManager()
        ctx = _make_ctx()
        ctx.state = DialogueState.WAITING

        ctx, action, text = dm.process(ctx, _intent(IntentType.REPEAT))
        assert action == DialogueAction.REPEAT_QUESTION
        assert ctx.state == DialogueState.REPEATING
        assert "Question 1" in text


# ---------------------------------------------------------------------------
# DialogueManager — REPHRASE intent
# ---------------------------------------------------------------------------

class TestRephraseIntent:
    def test_rephrase_rating_question(self):
        dm = DialogueManager()
        ctx = _make_ctx(question_type="rating")
        ctx.state = DialogueState.WAITING

        ctx, action, text = dm.process(ctx, _intent(IntentType.REPHRASE))
        assert action == DialogueAction.SPEAK_REPHRASE
        assert ctx.state == DialogueState.REPHRASING
        assert "1 to 10" in text

    def test_rephrase_free_text_question(self):
        dm = DialogueManager()
        ctx = _make_ctx(question_type="free_text")
        ctx.state = DialogueState.WAITING

        ctx, action, text = dm.process(ctx, _intent(IntentType.REPHRASE))
        assert action == DialogueAction.SPEAK_REPHRASE
        assert "wrong answer" in text.lower() or "freely" in text.lower()


# ---------------------------------------------------------------------------
# DialogueManager — ANSWER intent
# ---------------------------------------------------------------------------

class TestAnswerIntent:
    def test_high_confidence_answer_auto_accepted(self):
        dm = DialogueManager(FallbackConfig(skip_confirmation_above=0.85))
        ctx = _make_ctx(num_questions=2)
        ctx.state = DialogueState.WAITING

        ctx, action, _ = dm.process(
            ctx, _intent(IntentType.ANSWER, confidence=0.95, value="8")
        )
        # Should advance to next question
        assert action == DialogueAction.SPEAK_QUESTION
        assert ctx.current_question_index == 1
        assert ctx.answers.get("q1") == "8"

    def test_medium_confidence_answer_triggers_confirmation(self):
        dm = DialogueManager(FallbackConfig(
            require_confirmation_above=0.60,
            skip_confirmation_above=0.90,
        ))
        ctx = _make_ctx()
        ctx.state = DialogueState.WAITING

        ctx, action, text = dm.process(
            ctx, _intent(IntentType.ANSWER, confidence=0.75, value="7")
        )
        assert action == DialogueAction.CONFIRM_ANSWER
        assert ctx.state == DialogueState.CONFIRMING
        assert ctx.pending_answer == "7"
        assert "7" in text

    def test_low_confidence_answer_triggers_fallback(self):
        dm = DialogueManager(FallbackConfig(low_confidence_threshold=0.60))
        ctx = _make_ctx()
        ctx.state = DialogueState.WAITING

        ctx, action, _ = dm.process(
            ctx, _intent(IntentType.ANSWER, confidence=0.40, value="mumble")
        )
        assert action == DialogueAction.SPEAK_FALLBACK

    def test_last_question_answered_closes_session(self):
        dm = DialogueManager(FallbackConfig(skip_confirmation_above=0.85))
        ctx = _make_ctx(num_questions=1)
        ctx.state = DialogueState.WAITING

        ctx, action, text = dm.process(
            ctx, _intent(IntentType.ANSWER, confidence=0.95, value="9")
        )
        assert action == DialogueAction.SPEAK_CLOSING
        assert ctx.state == DialogueState.DONE


# ---------------------------------------------------------------------------
# DialogueManager — CONFIRMING state
# ---------------------------------------------------------------------------

class TestConfirmingState:
    def test_confirm_yes_accepts_answer(self):
        dm = DialogueManager()
        ctx = _make_ctx(num_questions=2)
        ctx.state = DialogueState.CONFIRMING
        ctx.pending_answer = "5"

        ctx, action, _ = dm.process(ctx, _intent(IntentType.CONFIRM_YES))
        assert ctx.answers.get("q1") == "5"
        assert ctx.current_question_index == 1

    def test_confirm_no_re_asks_question(self):
        dm = DialogueManager()
        ctx = _make_ctx()
        ctx.state = DialogueState.CONFIRMING
        ctx.pending_answer = "5"

        ctx, action, _ = dm.process(ctx, _intent(IntentType.CONFIRM_NO))
        assert ctx.state == DialogueState.WAITING
        assert ctx.pending_answer is None
        assert action == DialogueAction.SPEAK_QUESTION


# ---------------------------------------------------------------------------
# DialogueManager — NOT_NOW / SKIP
# ---------------------------------------------------------------------------

class TestNavigationIntents:
    def test_not_now_closes_session(self):
        dm = DialogueManager()
        ctx = _make_ctx()
        ctx.state = DialogueState.WAITING

        ctx, action, _ = dm.process(ctx, _intent(IntentType.NOT_NOW))
        assert action == DialogueAction.SPEAK_CLOSING
        assert ctx.state == DialogueState.DONE

    def test_skip_closes_session(self):
        dm = DialogueManager()
        ctx = _make_ctx()
        ctx.state = DialogueState.WAITING

        ctx, action, _ = dm.process(ctx, _intent(IntentType.SKIP))
        assert action == DialogueAction.SPEAK_CLOSING


# ---------------------------------------------------------------------------
# FallbackHandler — escalation
# ---------------------------------------------------------------------------

class TestFallbackHandler:
    def test_escalates_after_max_retries(self):
        handler = FallbackHandler(FallbackConfig(max_retries=3))
        ctx = _make_ctx()
        ctx.retry_count = 3

        result_ctx, action, text = handler.handle(ctx)
        assert action == DialogueAction.ESCALATE
        assert result_ctx.state == DialogueState.CLOSING

    def test_increments_retry_count(self):
        handler = FallbackHandler(FallbackConfig(max_retries=3))
        ctx = _make_ctx()
        ctx.retry_count = 0

        handler.handle(ctx)
        assert ctx.retry_count == 1

    def test_should_auto_accept_high_confidence(self):
        handler = FallbackHandler(FallbackConfig(skip_confirmation_above=0.90))
        assert handler.should_auto_accept(0.95) is True
        assert handler.should_auto_accept(0.89) is False

    def test_should_confirm_medium_confidence(self):
        handler = FallbackHandler(FallbackConfig(
            low_confidence_threshold=0.60,
            skip_confirmation_above=0.90,
        ))
        assert handler.should_confirm(0.75) is True
        assert handler.should_confirm(0.50) is False
        assert handler.should_confirm(0.95) is False


# ---------------------------------------------------------------------------
# NLU classifier integration
# ---------------------------------------------------------------------------

class TestNLUClassifierIntegration:
    """Verify the classifier produces intents the dialogue manager can handle."""

    def test_repeat_utterances(self):
        from app.voice.nlu.classifier import RuleBasedClassifier
        from app.voice.nlu.test_utterances import TEST_UTTERANCES

        clf = RuleBasedClassifier()
        for utterance in TEST_UTTERANCES[IntentType.REPEAT]:
            result = clf.classify(utterance)
            assert result.primary.intent_type == IntentType.REPEAT, (
                f"Expected REPEAT for '{utterance}', got {result.primary.intent_type}"
            )

    def test_not_now_utterances(self):
        from app.voice.nlu.classifier import RuleBasedClassifier
        from app.voice.nlu.test_utterances import TEST_UTTERANCES

        clf = RuleBasedClassifier()
        for utterance in TEST_UTTERANCES[IntentType.NOT_NOW]:
            result = clf.classify(utterance)
            assert result.primary.intent_type == IntentType.NOT_NOW, (
                f"Expected NOT_NOW for '{utterance}', got {result.primary.intent_type}"
            )

    def test_rating_answer_extraction(self):
        from app.voice.nlu.classifier import RuleBasedClassifier

        clf = RuleBasedClassifier()
        result = clf.classify("I'd give it a 7", question_type="rating")
        assert result.primary.intent_type == IntentType.ANSWER
        assert result.primary.extracted_value == "7"

    def test_mcq_letter_extraction(self):
        from app.voice.nlu.classifier import RuleBasedClassifier

        clf = RuleBasedClassifier()
        result = clf.classify("Option B", question_type="mcq")
        assert result.primary.intent_type == IntentType.ANSWER
        assert result.primary.extracted_value == "B"
