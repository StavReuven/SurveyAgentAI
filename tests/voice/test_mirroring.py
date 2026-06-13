"""SAA-73: Unit tests for the Psycho-Adaptive Voice Mirroring subsystem.

Covers:
  - SAA-71: FeatureExtractor (extraction + smoothing)
  - SAA-72: SessionCalibration (baseline lock + update)
  - SAA-74/75/76/77/78: MirroringPolicy (bounds, kill switch, monitoring flags)
"""

from __future__ import annotations

import pytest

from app.voice.mirroring.calibration import SessionCalibration
from app.voice.mirroring.features import FeatureExtractor, VocalFeatures
from app.voice.mirroring.policy import MirroringDecision, MirroringPolicy, MirroringSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_features(
    wpm: float = 120.0,
    pitch: float = 0.0,
    energy: float = 0.5,
    hesitation: float = 0.0,
    duration_ms: float = 3000.0,
) -> VocalFeatures:
    return VocalFeatures(
        speaking_rate_wpm=wpm,
        pitch_relative=pitch,
        energy_level=energy,
        hesitation_rate=hesitation,
        turn_duration_ms=duration_ms,
    )


def _calibrated(wpm: float = 120.0) -> SessionCalibration:
    """Return a SessionCalibration that is already fully calibrated."""
    extractor = FeatureExtractor()
    cal = SessionCalibration(calibration_turns=2)
    for _ in range(2):
        cal.update(_make_features(wpm=wpm), extractor)
    assert cal.is_calibrated
    return cal


# ---------------------------------------------------------------------------
# SAA-71: FeatureExtractor — extraction
# ---------------------------------------------------------------------------

class TestFeatureExtractor:
    def setup_method(self):
        self.extractor = FeatureExtractor()

    def test_speaking_rate_scales_with_word_count(self):
        # 10 words in 2 seconds → 300 wpm
        feat = self.extractor.extract("one two three four five six seven eight nine ten", 2_000.0)
        assert abs(feat.speaking_rate_wpm - 300.0) < 1.0

    def test_speaking_rate_minimum_duration_guard(self):
        # duration_ms=0 should not raise; saturates to duration_s=0.1
        feat = self.extractor.extract("hello", 0.0)
        assert feat.speaking_rate_wpm > 0

    def test_hesitation_rate_counts_markers(self):
        feat = self.extractor.extract("um I uh think so er maybe", 4_000.0)
        # "um", "uh", "er" = 3 hesitations out of 7 words
        assert abs(feat.hesitation_rate - 3 / 7) < 0.01

    def test_high_confidence_produces_positive_pitch(self):
        feat = self.extractor.extract("great", 1_000.0, confidence=0.95)
        assert feat.pitch_relative > 0.0

    def test_low_confidence_produces_negative_pitch(self):
        feat = self.extractor.extract("um yeah", 2_000.0, confidence=0.50)
        assert feat.pitch_relative < 0.0

    def test_pitch_clamps_to_minus_one_to_one(self):
        feat_low = self.extractor.extract("x", 500.0, confidence=0.0)
        feat_high = self.extractor.extract("x", 500.0, confidence=1.0)
        assert -1.0 <= feat_low.pitch_relative <= 1.0
        assert -1.0 <= feat_high.pitch_relative <= 1.0

    def test_energy_saturates_at_one(self):
        # Very fast speech (> 200 wpm) should give energy = 1.0
        feat = self.extractor.extract(" ".join(["x"] * 50), 500.0)  # ~6000 wpm
        assert feat.energy_level == 1.0

    # SAA-71: smoothing
    def test_smooth_favours_previous_with_low_alpha(self):
        current = _make_features(wpm=200.0)
        previous = _make_features(wpm=100.0)
        smoothed = self.extractor.smooth(current, previous, alpha=0.1)
        # 0.1*200 + 0.9*100 = 110
        assert abs(smoothed.speaking_rate_wpm - 110.0) < 0.1

    def test_smooth_alpha_one_returns_current(self):
        current = _make_features(wpm=200.0)
        previous = _make_features(wpm=100.0)
        smoothed = self.extractor.smooth(current, previous, alpha=1.0)
        assert smoothed.speaking_rate_wpm == 200.0

    def test_smooth_alpha_zero_returns_previous(self):
        current = _make_features(wpm=200.0)
        previous = _make_features(wpm=100.0)
        smoothed = self.extractor.smooth(current, previous, alpha=0.0)
        assert smoothed.speaking_rate_wpm == 100.0


# ---------------------------------------------------------------------------
# SAA-72: SessionCalibration
# ---------------------------------------------------------------------------

class TestSessionCalibration:
    def test_not_calibrated_initially(self):
        cal = SessionCalibration(calibration_turns=2)
        assert not cal.is_calibrated

    def test_calibrated_after_required_turns(self):
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=2)
        cal.update(_make_features(), extractor)
        assert not cal.is_calibrated
        cal.update(_make_features(), extractor)
        assert cal.is_calibrated

    def test_baseline_locked_after_calibration(self):
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=2)
        # Feed two identical turns
        cal.update(_make_features(wpm=120.0), extractor)
        cal.update(_make_features(wpm=120.0), extractor)
        assert cal.baseline is not None
        baseline_rate = cal.baseline.speaking_rate_wpm

        # Additional turns must not change the baseline
        cal.update(_make_features(wpm=999.0), extractor)
        assert cal.baseline.speaking_rate_wpm == baseline_rate

    def test_smoothed_updates_every_turn(self):
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=2)
        cal.update(_make_features(wpm=100.0), extractor)
        first_smoothed = cal.smoothed.speaking_rate_wpm
        cal.update(_make_features(wpm=200.0), extractor)
        assert cal.smoothed.speaking_rate_wpm != first_smoothed

    def test_to_dict_contains_expected_keys(self):
        cal = _calibrated()
        d = cal.to_dict()
        assert "turns_observed" in d
        assert "is_calibrated" in d
        assert "baseline" in d
        assert "smoothed" in d

    def test_turns_observed_increments(self):
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=3)
        for i in range(3):
            cal.update(_make_features(), extractor)
        assert cal.turns_observed == 3


# ---------------------------------------------------------------------------
# SAA-74/75: MirroringPolicy — rate and pitch bounds
# ---------------------------------------------------------------------------

class TestMirroringPolicyBounds:
    def _policy(self, **kwargs) -> MirroringPolicy:
        return MirroringPolicy(MirroringSettings(**kwargs))

    def test_returns_neutral_when_not_calibrated(self):
        policy = self._policy()
        cal = SessionCalibration(calibration_turns=5)  # not calibrated
        decision = policy.compute(cal, current_rapport=0.9)
        assert not decision.applied
        assert decision.speaking_rate == 1.0
        assert decision.pitch == 0.0
        assert "calibrating" in decision.reason

    def test_applies_after_calibration(self):
        policy = self._policy()
        cal = _calibrated()
        decision = policy.compute(cal, current_rapport=0.9)
        assert decision.applied

    def test_rate_stays_within_bounds(self):
        policy = self._policy(max_rate_delta=0.20)
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=2)
        # Baseline at 100 wpm
        cal.update(_make_features(wpm=100.0), extractor)
        cal.update(_make_features(wpm=100.0), extractor)
        # Now simulate extreme fast speech — 300 wpm
        cal.update(_make_features(wpm=300.0), extractor)

        decision = policy.compute(cal, current_rapport=0.9)
        assert decision.applied
        # SAA-75: rate must not exceed 1 + 0.20 = 1.20
        assert decision.speaking_rate <= 1.20 + 1e-6
        assert decision.speaking_rate >= 0.80 - 1e-6

    def test_pitch_stays_within_bounds(self):
        policy = self._policy(max_pitch_semitones=2.0)
        # Force extreme pitch_relative
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=2)
        feat = _make_features(pitch=1.0)  # max positive
        cal.update(feat, extractor)
        cal.update(feat, extractor)

        decision = policy.compute(cal, current_rapport=0.9)
        assert decision.applied
        assert abs(decision.pitch) <= 2.0 + 1e-6


# ---------------------------------------------------------------------------
# SAA-76: Kill switch
# ---------------------------------------------------------------------------

class TestMirroringPolicyKillSwitch:
    def test_kill_switch_triggers_below_threshold(self):
        policy = MirroringPolicy(MirroringSettings(kill_switch_rapport_threshold=0.60))
        cal = _calibrated()
        decision = policy.compute(cal, current_rapport=0.55)
        assert not decision.applied
        assert "kill_switch" in decision.reason

    def test_no_kill_switch_above_threshold(self):
        policy = MirroringPolicy(MirroringSettings(kill_switch_rapport_threshold=0.60))
        cal = _calibrated()
        decision = policy.compute(cal, current_rapport=0.65)
        assert decision.applied

    def test_kill_switch_returns_neutral_rate_and_pitch(self):
        policy = MirroringPolicy(MirroringSettings(kill_switch_rapport_threshold=0.70))
        cal = _calibrated()
        decision = policy.compute(cal, current_rapport=0.50)
        assert decision.speaking_rate == 1.0
        assert decision.pitch == 0.0

    def test_disabled_policy_never_applies(self):
        policy = MirroringPolicy(MirroringSettings(enabled=False))
        cal = _calibrated()
        decision = policy.compute(cal, current_rapport=0.99)
        assert not decision.applied
        assert decision.reason == "disabled"


# ---------------------------------------------------------------------------
# SAA-77: logging (smoke test — just ensure no exception)
# ---------------------------------------------------------------------------

class TestMirroringLogging:
    def test_kill_switch_logs_at_info(self, caplog):
        import logging
        policy = MirroringPolicy(MirroringSettings(kill_switch_rapport_threshold=0.80))
        cal = _calibrated()
        with caplog.at_level(logging.INFO, logger="app.voice.mirroring.policy"):
            policy.compute(cal, current_rapport=0.50)
        assert any("kill_switch" in r.message for r in caplog.records)

    def test_applied_mirroring_logs_at_info(self, caplog):
        import logging
        policy = MirroringPolicy(MirroringSettings())
        cal = _calibrated()
        with caplog.at_level(logging.INFO, logger="app.voice.mirroring.policy"):
            policy.compute(cal, current_rapport=0.90)
        assert any("mirroring applied" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# SAA-78: monitoring flags
# ---------------------------------------------------------------------------

class TestMonitoringFlags:
    def test_flags_structure(self):
        policy = MirroringPolicy(MirroringSettings(
            enabled=True,
            kill_switch_rapport_threshold=0.50,
        ))
        cal = _calibrated()
        flags = policy.monitoring_flags(cal, current_rapport=0.80)
        assert flags["mirroring_enabled"] is True
        assert flags["calibrated"] is True
        assert flags["rapport_above_threshold"] is True
        assert flags["kill_switch_threshold"] == 0.50
        assert "turns_observed" in flags

    def test_flags_report_not_calibrated(self):
        policy = MirroringPolicy(MirroringSettings())
        cal = SessionCalibration(calibration_turns=10)
        flags = policy.monitoring_flags(cal, current_rapport=0.90)
        assert flags["calibrated"] is False

    def test_flags_rapport_below_threshold(self):
        policy = MirroringPolicy(MirroringSettings(kill_switch_rapport_threshold=0.70))
        cal = _calibrated()
        flags = policy.monitoring_flags(cal, current_rapport=0.60)
        assert flags["rapport_above_threshold"] is False


# ---------------------------------------------------------------------------
# SAA-71: VocalFeatures.to_dict
# ---------------------------------------------------------------------------

class TestVocalFeaturesToDict:
    def test_to_dict_keys(self):
        feat = _make_features()
        d = feat.to_dict()
        assert set(d.keys()) == {
            "speaking_rate_wpm", "pitch_relative", "energy_level",
            "hesitation_rate", "turn_duration_ms",
        }


# ---------------------------------------------------------------------------
# Improvements: sliding baseline, rapport-weighted rate, hesitation pitch
# ---------------------------------------------------------------------------

class TestSlidingBaseline:
    def test_baseline_frozen_when_drift_alpha_zero(self):
        extractor = FeatureExtractor()
        cal = _calibrated(wpm=100.0)
        original_baseline = cal.baseline.speaking_rate_wpm

        # Many fast turns — baseline must not move
        for _ in range(5):
            cal.update(_make_features(wpm=300.0), extractor, baseline_drift_alpha=0.0)
        assert cal.baseline.speaking_rate_wpm == original_baseline

    def test_baseline_drifts_when_alpha_positive(self):
        extractor = FeatureExtractor()
        cal = _calibrated(wpm=100.0)
        original_baseline = cal.baseline.speaking_rate_wpm

        # Many fast turns with drift enabled — baseline should move toward 300
        for _ in range(10):
            cal.update(_make_features(wpm=300.0), extractor, baseline_drift_alpha=0.10)
        assert cal.baseline.speaking_rate_wpm > original_baseline

    def test_baseline_drift_does_not_overshoot(self):
        extractor = FeatureExtractor()
        cal = _calibrated(wpm=100.0)

        for _ in range(50):
            cal.update(_make_features(wpm=200.0), extractor, baseline_drift_alpha=0.10)
        # With alpha=0.10 and 50 turns it should converge but not overshoot 200
        assert cal.baseline.speaking_rate_wpm <= 200.0 + 1.0


class TestRapportWeightedRate:
    def _policy_with_weight(self) -> MirroringPolicy:
        return MirroringPolicy(MirroringSettings(rapport_rate_weight=True))

    def test_high_rapport_gives_full_mirror(self):
        policy = self._policy_with_weight()
        extractor = FeatureExtractor()
        cal = SessionCalibration(calibration_turns=2)
        cal.update(_make_features(wpm=100.0), extractor)
        cal.update(_make_features(wpm=100.0), extractor)
        # Caller speeds up: smoothed > baseline
        cal.update(_make_features(wpm=200.0), extractor)

        decision_high = policy.compute(cal, current_rapport=0.90)
        decision_mid = policy.compute(cal, current_rapport=0.70)

        # High rapport should rate-adjust more than mid rapport
        assert decision_high.speaking_rate >= decision_mid.speaking_rate

    def test_low_rapport_nudges_rate_down(self):
        policy = self._policy_with_weight()
        cal = _calibrated(wpm=120.0)
        # rapport just above kill-switch but below 0.65 → de-escalation bias
        decision = policy.compute(cal, current_rapport=0.55)
        # Kill switch fires below 0.50, but 0.55 is above it; rate should still be < 1.0
        # Actually kill switch threshold is 0.50, so 0.55 triggers the nudge path
        assert decision.applied
        assert decision.speaking_rate < 1.0


class TestHesitationPitchPenalty:
    def test_hesitant_speech_lowers_pitch(self):
        policy = MirroringPolicy(MirroringSettings())
        extractor = FeatureExtractor()

        cal_calm = SessionCalibration(calibration_turns=2)
        feat_calm = _make_features(pitch=0.5, hesitation=0.0)
        cal_calm.update(feat_calm, extractor)
        cal_calm.update(feat_calm, extractor)

        cal_hesitant = SessionCalibration(calibration_turns=2)
        feat_hesitant = _make_features(pitch=0.5, hesitation=0.6)
        cal_hesitant.update(feat_hesitant, extractor)
        cal_hesitant.update(feat_hesitant, extractor)

        d_calm = policy.compute(cal_calm, current_rapport=0.85)
        d_hesitant = policy.compute(cal_hesitant, current_rapport=0.85)

        # Same raw pitch_relative but hesitant caller should yield lower pitch output
        assert d_hesitant.pitch < d_calm.pitch
