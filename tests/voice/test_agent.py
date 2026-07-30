"""Unit tests for AgentAI — RuleBasedFallback and AgentDecision schema."""

from __future__ import annotations

import pytest

from app.voice.agent.fallback import RuleBasedFallback
from app.voice.agent.schema import AgentDecision, AgentIntent, NextAction
from app.voice.dialogue.fsm import QuestionContext
from app.voice.nlu.schema import IntentType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _q(question_type: str = "rating", options: list | None = None) -> QuestionContext:
    cfg = {}
    if options:
        cfg["options"] = options
    return QuestionContext(
        question_id=1,
        question_key="q1",
        prompt="How would you rate our service from 1 to 10?",
        question_type=question_type,
        order_index=0,
        config=cfg,
    )


fb = RuleBasedFallback()


# ---------------------------------------------------------------------------
# Rating extraction
# ---------------------------------------------------------------------------

class TestRatingExtraction:
    def test_digit_in_answer(self):
        d = fb.analyze("I give it an 8", _q("rating"))
        assert d.intent == AgentIntent.ANSWER
        assert d.should_save_answer is True
        assert d.extracted_answer is not None
        assert d.extracted_answer.value == 8

    def test_word_number(self):
        d = fb.analyze("seven", _q("rating"))
        assert d.extracted_answer.value == 7

    def test_sentiment_excellent(self):
        d = fb.analyze("I think it was excellent", _q("rating"))
        assert d.extracted_answer.value == 5

    def test_sentiment_pretty_good(self):
        d = fb.analyze("pretty good honestly", _q("rating"))
        assert d.extracted_answer.value == 4

    def test_sentiment_terrible(self):
        d = fb.analyze("it was terrible", _q("rating"))
        assert d.extracted_answer.value == 1

    def test_ten(self):
        d = fb.analyze("definitely a ten", _q("rating"))
        assert d.extracted_answer.value == 10

    def test_natural_language_phrase(self):
        d = fb.analyze("maybe a seven I guess", _q("rating"))
        assert d.extracted_answer.value == 7


# ---------------------------------------------------------------------------
# Yes / No
# ---------------------------------------------------------------------------

class TestYesNo:
    def test_yes_variants(self):
        for phrase in ("yes", "yeah", "absolutely", "sure", "of course"):
            d = fb.analyze(phrase, _q("yes_no"))
            assert d.extracted_answer.value is True, f"Expected True for '{phrase}'"

    def test_no_variants(self):
        for phrase in ("no", "nope", "not really", "never"):
            d = fb.analyze(phrase, _q("yes_no"))
            assert d.extracted_answer.value is False, f"Expected False for '{phrase}'"


# ---------------------------------------------------------------------------
# Multiple choice
# ---------------------------------------------------------------------------

class TestMCQ:
    def test_letter_answer(self):
        d = fb.analyze("I choose B", _q("mcq"))
        assert d.extracted_answer.value == "B"

    def test_ordinal_first(self):
        d = fb.analyze("the first option", _q("mcq"))
        assert d.extracted_answer.value == "A"

    def test_ordinal_second(self):
        d = fb.analyze("second one", _q("mcq"))
        assert d.extracted_answer.value == "B"

    def test_ordinal_third(self):
        d = fb.analyze("I think the third", _q("mcq"))
        assert d.extracted_answer.value == "C"

    def test_option_text_match(self):
        q = _q("mcq", options=["Search", "Notifications", "Dashboard", "Reports"])
        d = fb.analyze("I mostly use notifications", q)
        assert d.extracted_answer.value == "B"

    def test_affirmative_maps_to_A(self):
        q = _q("mcq", options=["Yes", "No"])
        d = fb.analyze("of course, yes", q)
        assert d.extracted_answer.value == "A"

    def test_negative_maps_to_B(self):
        q = _q("mcq", options=["Yes", "No"])
        d = fb.analyze("nope not really", q)
        assert d.extracted_answer.value == "B"

    def test_affirmative_without_options(self):
        d = fb.analyze("absolutely", _q("mcq"))
        assert d.extracted_answer.value == "A"


# ---------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------

class TestFreeText:
    def test_any_substantive_text(self):
        d = fb.analyze("the service was excellent and I loved the experience", _q("free_text"))
        assert d.intent == AgentIntent.ANSWER
        assert d.should_save_answer is True
        assert d.extracted_answer.type == "free_text"

    def test_very_short_text_unclear(self):
        d = fb.analyze("ok", _q("free_text"))
        # "ok" matches _RATING_WORDS as 3, but q_type is free_text — handled via free_text branch
        # Length check: len("ok") == 2, so it's <= 2 → unclear
        assert d.intent == AgentIntent.UNCLEAR

    def test_preserves_original_text(self):
        transcript = "the delivery was faster than expected"
        d = fb.analyze(transcript, _q("free_text"))
        assert d.extracted_answer.raw_text == transcript


# ---------------------------------------------------------------------------
# Meta-intents
# ---------------------------------------------------------------------------

class TestMetaIntents:
    def test_repeat_question(self):
        for phrase in ("could you repeat that", "say that again", "pardon", "what did you say"):
            d = fb.analyze(phrase, _q())
            assert d.intent == AgentIntent.REPEAT_QUESTION, f"Failed for '{phrase}'"
            assert d.next_action == NextAction.REPEAT

    def test_rephrase_question(self):
        for phrase in ("I don't understand", "can you explain", "clarify please"):
            d = fb.analyze(phrase, _q())
            assert d.intent == AgentIntent.REPHRASE_QUESTION, f"Failed for '{phrase}'"
            assert d.next_action == NextAction.REPHRASE

    def test_not_now(self):
        for phrase in ("not now", "call me back", "I'm busy", "bad time"):
            d = fb.analyze(phrase, _q())
            assert d.intent == AgentIntent.NOT_NOW, f"Failed for '{phrase}'"
            assert d.next_action == NextAction.RESCHEDULE

    def test_opt_out(self):
        for phrase in ("stop calling me", "remove me", "do not call"):
            d = fb.analyze(phrase, _q())
            assert d.intent == AgentIntent.OPT_OUT, f"Failed for '{phrase}'"
            assert d.next_action == NextAction.OPT_OUT

    def test_escalate(self):
        for phrase in ("I want to speak to a human", "get me a manager", "transfer me"):
            d = fb.analyze(phrase, _q())
            assert d.intent == AgentIntent.ESCALATE, f"Failed for '{phrase}'"
            assert d.next_action == NextAction.ESCALATE

    def test_unclear_answer(self):
        d = fb.analyze("blah blah", _q("rating"))
        assert d.intent == AgentIntent.UNCLEAR
        assert d.should_save_answer is False

    def test_empty_transcript(self):
        # Service layer handles empty before calling fallback, but fallback should still be safe
        d = fb.analyze("", _q())
        # Falls through to unclear (no patterns match, no text)
        assert d.should_save_answer is False

    def test_priority_opt_out_over_answer(self):
        # Even if the text contains a number, opt-out takes precedence
        d = fb.analyze("stop calling me I give you a seven", _q("rating"))
        assert d.intent == AgentIntent.OPT_OUT

    def test_profanity_triggers_escalate(self):
        d = fb.analyze("fuck you", _q())
        assert d.intent == AgentIntent.ESCALATE
        assert d.next_action == NextAction.ESCALATE
        assert d.should_save_answer is False
        assert "respectful" in d.response_text.lower()

    def test_profanity_priority_over_everything(self):
        # Even if user says a valid answer alongside profanity, escalate wins
        d = fb.analyze("fuck this survey I give it a ten", _q("rating"))
        assert d.intent == AgentIntent.ESCALATE

    def test_profanity_hebrew_response(self):
        d = fb.analyze("fuck you", _q(), language="he")
        assert d.intent == AgentIntent.ESCALATE
        assert "מכובדת" in d.response_text


# ---------------------------------------------------------------------------
# to_nlu_intent() FSM bridge
# ---------------------------------------------------------------------------

class TestToNluIntent:
    def _answer_decision(self, *, save: bool, next_action=NextAction.CONTINUE) -> AgentDecision:
        from app.voice.agent.schema import ExtractedAnswer
        return AgentDecision(
            intent=AgentIntent.ANSWER,
            confidence=0.85,
            next_action=next_action,
            response_text="Thank you.",
            should_save_answer=save,
            extracted_answer=ExtractedAnswer(8, "rating", "eight") if save else None,
        )

    def test_answer_with_save_gets_high_confidence(self):
        intent = self._answer_decision(save=True).to_nlu_intent()
        assert intent.intent_type == IntentType.ANSWER
        assert intent.confidence >= 0.90

    def test_answer_without_save_downgrades_to_unknown(self):
        intent = self._answer_decision(
            save=False, next_action=NextAction.ASK_CLARIFICATION
        ).to_nlu_intent()
        assert intent.intent_type == IntentType.UNKNOWN

    def test_clarification_gets_low_confidence(self):
        d = AgentDecision(
            intent=AgentIntent.UNCLEAR,
            confidence=0.30,
            next_action=NextAction.ASK_CLARIFICATION,
            response_text="Could you repeat?",
            should_save_answer=False,
        )
        intent = d.to_nlu_intent()
        assert intent.confidence <= 0.50

    def test_repeat_maps_to_repeat(self):
        d = AgentDecision(
            intent=AgentIntent.REPEAT_QUESTION,
            confidence=0.88,
            next_action=NextAction.REPEAT,
            response_text="",
            should_save_answer=False,
        )
        assert d.to_nlu_intent().intent_type == IntentType.REPEAT

    def test_opt_out_maps_to_not_now(self):
        d = AgentDecision(
            intent=AgentIntent.OPT_OUT,
            confidence=0.92,
            next_action=NextAction.OPT_OUT,
            response_text="Understood.",
            should_save_answer=False,
        )
        assert d.to_nlu_intent().intent_type == IntentType.NOT_NOW

    def test_extracted_value_passed_through(self):
        from app.voice.agent.schema import ExtractedAnswer
        d = AgentDecision(
            intent=AgentIntent.ANSWER,
            confidence=0.90,
            next_action=NextAction.CONTINUE,
            response_text="Thank you.",
            should_save_answer=True,
            extracted_answer=ExtractedAnswer(True, "yes_no", "yes"),
        )
        intent = d.to_nlu_intent()
        assert intent.extracted_value == "yes"


# ---------------------------------------------------------------------------
# Hebrew language support
# ---------------------------------------------------------------------------

class TestHebrew:
    def test_rating_hebrew(self):
        d = fb.analyze("אני נותן שמונה", _q("rating"), language="he")
        # "שמונה" is not in the English patterns, but digit "8" may not be present
        # → falls through to unclear, which is acceptable for a rule-based fallback
        assert d is not None   # should not crash

    def test_opt_out_in_english_still_works_for_hebrew_sessions(self):
        d = fb.analyze("stop calling me", _q(), language="he")
        assert d.intent == AgentIntent.OPT_OUT
        assert any(ord(c) > 0x590 for c in d.response_text), "Expected Hebrew response text"

    def test_repeat_hebrew_response(self):
        d = fb.analyze("say that again", _q(), language="he")
        assert d.intent == AgentIntent.REPEAT_QUESTION


# ---------------------------------------------------------------------------
# Structured validation on free_text questions (age/city — QuestionContext
# .config["validate"]), the mechanism behind the auto-asked intake questions.
# ---------------------------------------------------------------------------

def _validated_q(validate_key: str) -> QuestionContext:
    return QuestionContext(
        question_id=-1,
        question_key=f"auto_{validate_key}",
        prompt="What is your age?" if validate_key == "age" else "Which city do you live in?",
        question_type="free_text",
        order_index=0,
        config={"validate": validate_key},
    )


class TestAgeValidation:
    def test_plain_number_accepted(self):
        d = fb.analyze("34", _validated_q("age"))
        assert d.intent == AgentIntent.ANSWER
        assert d.should_save_answer is True
        assert d.extracted_answer.value == 34

    def test_number_in_sentence_accepted(self):
        d = fb.analyze("I am 29 years old", _validated_q("age"))
        assert d.intent == AgentIntent.ANSWER
        assert d.extracted_answer.value == 29

    def test_non_numeric_rejected_and_reasks(self):
        d = fb.analyze("banana", _validated_q("age"))
        assert d.intent == AgentIntent.UNCLEAR
        assert d.next_action == NextAction.ASK_CLARIFICATION
        assert d.should_save_answer is False
        assert "number" in d.response_text.lower()

    def test_i_dont_know_rejected(self):
        d = fb.analyze("I don't know", _validated_q("age"))
        assert d.intent == AgentIntent.UNCLEAR
        assert d.should_save_answer is False

    def test_out_of_range_rejected(self):
        d = fb.analyze("400", _validated_q("age"))
        assert d.intent == AgentIntent.UNCLEAR
        assert d.should_save_answer is False

    def test_hebrew_clarification_text(self):
        d = fb.analyze("לא יודע", _validated_q("age"), language="he")
        assert d.intent == AgentIntent.UNCLEAR
        assert any(ord(c) > 0x590 for c in d.response_text)


class TestCityValidation:
    def test_city_name_accepted(self):
        d = fb.analyze("Tel Aviv", _validated_q("city"))
        assert d.intent == AgentIntent.ANSWER
        assert d.extracted_answer.value == "Tel Aviv"

    def test_small_town_not_in_any_fixed_list_still_accepted(self):
        # Deliberately lenient: no closed city list, so a real town the
        # validator has never heard of must still be accepted.
        d = fb.analyze("Kfar Yona", _validated_q("city"))
        assert d.intent == AgentIntent.ANSWER

    def test_i_dont_know_rejected(self):
        d = fb.analyze("I don't know", _validated_q("city"))
        assert d.intent == AgentIntent.UNCLEAR
        assert d.should_save_answer is False

    def test_pure_number_rejected(self):
        d = fb.analyze("34", _validated_q("city"))
        assert d.intent == AgentIntent.UNCLEAR

    def test_too_short_rejected(self):
        d = fb.analyze("a", _validated_q("city"))
        assert d.intent == AgentIntent.UNCLEAR


class TestUnvalidatedFreeTextUnaffected:
    """The generic (non-validated) free_text path — used by every ordinary
    survey free_text question — must behave exactly as before."""

    def test_arbitrary_text_still_accepted(self):
        d = fb.analyze("the packaging could have been sturdier", _q("free_text"))
        assert d.intent == AgentIntent.ANSWER
        assert d.should_save_answer is True


# ---------------------------------------------------------------------------
# Hebrew opt-out / not-now detection — previously _OPT_OUT and _NOT_NOW only
# matched English phrases (see TestHebrew.test_opt_out_in_english_still_works_
# for_hebrew_sessions above), so a Hebrew caller saying "don't call me again"
# in their own language was never actually detected as an opt-out.
# ---------------------------------------------------------------------------

class TestHebrewOptOutDetection:
    def test_dont_call_again(self):
        d = fb.analyze("אל תתקשרו אליי יותר", _q(), language="he")
        assert d.intent == AgentIntent.OPT_OUT
        assert d.next_action == NextAction.OPT_OUT

    def test_remove_me_from_list(self):
        d = fb.analyze("תסירו אותי מהרשימה בבקשה", _q(), language="he")
        assert d.intent == AgentIntent.OPT_OUT

    def test_not_interested_anymore(self):
        d = fb.analyze("לא מעוניין יותר", _q(), language="he")
        assert d.intent == AgentIntent.OPT_OUT

    def test_response_text_is_hebrew(self):
        d = fb.analyze("אל תתקשרו אליי יותר", _q(), language="he")
        assert any(0x590 <= ord(c) <= 0x5FF for c in d.response_text)

    def test_plain_answer_not_misdetected_as_opt_out(self):
        # Guard against over-broad Hebrew patterns misfiring on an ordinary answer.
        d = fb.analyze("שמונה", _q("rating"))
        assert d.intent != AgentIntent.OPT_OUT


class TestHebrewNotNowDetection:
    def test_not_now(self):
        d = fb.analyze("לא עכשיו", _q(), language="he")
        assert d.intent == AgentIntent.NOT_NOW
        assert d.next_action == NextAction.RESCHEDULE

    def test_call_back_later(self):
        d = fb.analyze("תתקשרו אליי אחר כך", _q(), language="he")
        assert d.intent == AgentIntent.NOT_NOW

    def test_busy_right_now(self):
        d = fb.analyze("אני עסוקה כרגע", _q(), language="he")
        assert d.intent == AgentIntent.NOT_NOW


# ---------------------------------------------------------------------------
# requested_callback_at — NOT_NOW should carry a parsed time when the caller
# named one, and stay None (generic retry policy applies) when they didn't.
# ---------------------------------------------------------------------------

class TestRequestedCallbackAt:
    def test_none_for_bare_not_now(self):
        d = fb.analyze("not now", _q())
        assert d.intent == AgentIntent.NOT_NOW
        assert d.requested_callback_at is None

    def test_none_for_bare_hebrew_not_now(self):
        d = fb.analyze("לא עכשיו", _q(), language="he")
        assert d.requested_callback_at is None

    def test_set_when_caller_names_a_time_english(self):
        d = fb.analyze("call me back tomorrow evening", _q())
        assert d.intent == AgentIntent.NOT_NOW
        assert d.requested_callback_at is not None
        assert d.requested_callback_at.hour == 19

    def test_set_when_caller_names_a_time_hebrew(self):
        d = fb.analyze("תתקשרו אליי מחר בערב", _q(), language="he")
        assert d.intent == AgentIntent.NOT_NOW
        assert d.requested_callback_at is not None
        assert d.requested_callback_at.hour == 19

    def test_included_in_to_dict(self):
        d = fb.analyze("call me back in an hour", _q())
        payload = d.to_dict()
        assert payload["requested_callback_at"] is not None

    def test_none_serialises_as_null_in_to_dict(self):
        d = fb.analyze("not now", _q())
        payload = d.to_dict()
        assert payload["requested_callback_at"] is None
