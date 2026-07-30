"""Unit tests for app.voice.nlu.validators, plus the RuleBasedClassifier's
question_config-aware free_text validation (the code path used when no
AgentAIService is injected — e.g. VoicePipeline() with no agent_service)."""
from __future__ import annotations

from app.voice.nlu.classifier import RuleBasedClassifier
from app.voice.nlu.schema import IntentType
from app.voice.nlu.validators import validate_age, validate_city


# ---------------------------------------------------------------------------
# validate_age
# ---------------------------------------------------------------------------

class TestValidateAge:
    def test_plain_number(self):
        assert validate_age("34") == (True, 34)

    def test_number_embedded_in_sentence(self):
        assert validate_age("I'm 29 years old") == (True, 29)

    def test_no_number_invalid(self):
        ok, val = validate_age("banana")
        assert ok is False and val is None

    def test_empty_invalid(self):
        ok, val = validate_age("")
        assert ok is False

    def test_zero_out_of_range(self):
        ok, _ = validate_age("0")
        assert ok is False

    def test_too_large_out_of_range(self):
        ok, _ = validate_age("999")
        assert ok is False

    def test_boundary_values_valid(self):
        assert validate_age("1") == (True, 1)
        assert validate_age("120") == (True, 120)


# ---------------------------------------------------------------------------
# validate_city
# ---------------------------------------------------------------------------

class TestValidateCity:
    def test_normal_city_name(self):
        ok, val = validate_city("Tel Aviv")
        assert ok is True and val == "Tel Aviv"

    def test_unlisted_small_town_still_valid(self):
        # No closed city list on purpose — any plausible place name passes.
        ok, _ = validate_city("Zichron Yaakov")
        assert ok is True

    def test_dont_know_invalid(self):
        ok, _ = validate_city("I don't know")
        assert ok is False

    def test_hebrew_dont_know_invalid(self):
        ok, _ = validate_city("לא יודע")
        assert ok is False

    def test_pure_digits_invalid(self):
        ok, _ = validate_city("12345")
        assert ok is False

    def test_single_char_invalid(self):
        ok, _ = validate_city("a")
        assert ok is False

    def test_empty_invalid(self):
        ok, _ = validate_city("")
        assert ok is False


# ---------------------------------------------------------------------------
# RuleBasedClassifier — question_config["validate"] wiring
# ---------------------------------------------------------------------------

class TestClassifierStructuredValidation:
    def test_valid_age_answer_is_answer_intent(self):
        clf = RuleBasedClassifier()
        result = clf.classify("42", question_type="free_text", question_config={"validate": "age"})
        assert result.primary.intent_type == IntentType.ANSWER
        assert result.primary.extracted_value == "42"

    def test_invalid_age_answer_is_unknown_intent(self):
        clf = RuleBasedClassifier()
        result = clf.classify("banana", question_type="free_text", question_config={"validate": "age"})
        assert result.primary.intent_type == IntentType.UNKNOWN

    def test_valid_city_answer_is_answer_intent(self):
        clf = RuleBasedClassifier()
        result = clf.classify("Haifa", question_type="free_text", question_config={"validate": "city"})
        assert result.primary.intent_type == IntentType.ANSWER

    def test_invalid_city_answer_is_unknown_intent(self):
        clf = RuleBasedClassifier()
        result = clf.classify("I don't know", question_type="free_text", question_config={"validate": "city"})
        assert result.primary.intent_type == IntentType.UNKNOWN

    def test_unvalidated_free_text_unaffected(self):
        """No question_config at all (the default) — every ordinary survey
        free_text question — must behave exactly as before this change."""
        clf = RuleBasedClassifier()
        result = clf.classify("the packaging could have been sturdier", question_type="free_text")
        assert result.primary.intent_type == IntentType.ANSWER
        assert result.primary.extracted_value == "the packaging could have been sturdier"
