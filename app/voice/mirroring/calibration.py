"""SAA-72: Per-session vocal calibration — baseline the caller's features.

The calibrator accumulates smoothed VocalFeatures across turns.  Once
`calibration_turns` have been observed the smoothed state is snapshotted
as a stable *baseline* that the MirroringPolicy compares against on every
subsequent turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .features import FeatureExtractor, VocalFeatures


@dataclass
class SessionCalibration:
    """Mutable per-session state for vocal feature tracking."""

    calibration_turns: int = 2       # turns required before baseline is locked
    turns_observed: int = 0
    smoothed: VocalFeatures | None = None   # running EMA of features
    baseline: VocalFeatures | None = None   # snapshot after calibration_turns

    @property
    def is_calibrated(self) -> bool:
        return self.turns_observed >= self.calibration_turns and self.baseline is not None

    def update(
        self,
        features: VocalFeatures,
        extractor: FeatureExtractor,
        alpha: float = 0.3,
        baseline_drift_alpha: float = 0.0,
    ) -> VocalFeatures:
        """Ingest one turn's features, smooth them, and return the updated EMA.

        Locks the baseline snapshot once calibration_turns is reached.
        After lock, if baseline_drift_alpha > 0 the baseline slowly drifts
        toward the caller's evolving vocal style (sliding baseline).
        """
        self.turns_observed += 1

        if self.smoothed is None:
            self.smoothed = features
        else:
            self.smoothed = extractor.smooth(features, self.smoothed, alpha=alpha)

        if self.turns_observed == self.calibration_turns:
            baseline_features = self.smoothed
            self.baseline = VocalFeatures(
                speaking_rate_wpm=baseline_features.speaking_rate_wpm,
                pitch_relative=baseline_features.pitch_relative,
                energy_level=baseline_features.energy_level,
                hesitation_rate=baseline_features.hesitation_rate,
                turn_duration_ms=baseline_features.turn_duration_ms,
            )
        elif self.baseline is not None and baseline_drift_alpha > 0:
            # Sliding baseline: gently pull toward current smoothed so mirroring
            # remains meaningful as the caller's style evolves mid-call.
            self.baseline = extractor.smooth(self.smoothed, self.baseline, alpha=baseline_drift_alpha)

        return self.smoothed

    def to_dict(self) -> dict:
        return {
            "turns_observed": self.turns_observed,
            "is_calibrated": self.is_calibrated,
            "calibration_turns": self.calibration_turns,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "smoothed": self.smoothed.to_dict() if self.smoothed else None,
        }
