"""SAA-71: Vocal feature extraction pipeline with exponential smoothing.

Features are derived from the transcript text and STT metadata.  In a
production system pitch and energy would come from DSP on raw audio bytes;
here they are estimated from confidence and speaking-rate proxies.
"""

from __future__ import annotations

from dataclasses import dataclass

# Words treated as hesitation markers (English + Hebrew transliteration common forms)
_HESITATION_MARKERS = frozenset(
    {"um", "uh", "er", "hmm", "ah", "ehm", "like", "well", "אממ", "אה", "אהמ"}
)


@dataclass
class VocalFeatures:
    """Extracted vocal characteristics for one caller turn."""

    speaking_rate_wpm: float   # words per minute
    pitch_relative: float      # −1.0 … +1.0  (proxy: derived from STT confidence)
    energy_level: float        # 0.0 … 1.0    (proxy: derived from speaking rate)
    hesitation_rate: float     # hesitation markers / total words
    turn_duration_ms: float    # audio duration of this turn

    def to_dict(self) -> dict:
        return {
            "speaking_rate_wpm": round(self.speaking_rate_wpm, 1),
            "pitch_relative": round(self.pitch_relative, 3),
            "energy_level": round(self.energy_level, 3),
            "hesitation_rate": round(self.hesitation_rate, 3),
            "turn_duration_ms": round(self.turn_duration_ms, 1),
        }


class FeatureExtractor:
    """Extract VocalFeatures from a transcript string and STT metadata.

    Parameters
    ----------
    alpha:
        Default smoothing coefficient used by :meth:`smooth`.
        A value of 0.3 means the current turn contributes 30 % to the
        smoothed estimate while the prior carries 70 %.
    """

    def extract(
        self,
        text: str,
        duration_ms: float,
        confidence: float = 0.8,
    ) -> VocalFeatures:
        """Compute features from a single turn's transcript."""
        words = text.lower().split()
        word_count = max(1, len(words))
        duration_s = max(0.1, duration_ms / 1000.0)

        speaking_rate_wpm = (word_count / duration_s) * 60.0

        hesitations = sum(
            1 for w in words if w.strip(".,!?;:") in _HESITATION_MARKERS
        )
        hesitation_rate = hesitations / word_count

        # Proxy: maps confidence 0.5 → −0.4, 0.7 → 0.0, 0.9 → +0.4
        pitch_relative = max(-1.0, min(1.0, (confidence - 0.7) * 2.0))

        # Proxy: faster speech ≈ more energy (saturates at 200 wpm)
        energy_level = min(1.0, speaking_rate_wpm / 200.0)

        return VocalFeatures(
            speaking_rate_wpm=speaking_rate_wpm,
            pitch_relative=pitch_relative,
            energy_level=energy_level,
            hesitation_rate=hesitation_rate,
            turn_duration_ms=duration_ms,
        )

    def smooth(
        self,
        current: VocalFeatures,
        previous: VocalFeatures,
        alpha: float = 0.3,
    ) -> VocalFeatures:
        """Exponential moving average: new = alpha*current + (1−alpha)*previous."""
        b = 1.0 - alpha
        return VocalFeatures(
            speaking_rate_wpm=alpha * current.speaking_rate_wpm + b * previous.speaking_rate_wpm,
            pitch_relative=alpha * current.pitch_relative + b * previous.pitch_relative,
            energy_level=alpha * current.energy_level + b * previous.energy_level,
            hesitation_rate=alpha * current.hesitation_rate + b * previous.hesitation_rate,
            turn_duration_ms=current.turn_duration_ms,
        )
