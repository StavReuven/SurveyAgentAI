"""SAA-74: Safe Mirroring Policy — bounded TTS adjustments + kill switch.

Guarantees (SAA-75):
  - speaking_rate ∈ [1 − max_rate_delta, 1 + max_rate_delta]
  - pitch       ∈ [−max_pitch_semitones, +max_pitch_semitones]

Kill switch (SAA-76):
  - Reverts to neutral (rate=1.0, pitch=0.0) when:
      • mirroring is globally disabled
      • session rapport drops below kill_switch_rapport_threshold

Logging (SAA-77): every decision is emitted via the standard `logging` module.

Monitoring flags (SAA-78): :meth:`monitoring_flags` exposes a dict of
boolean/numeric flags the API layer can include in session responses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .calibration import SessionCalibration

logger = logging.getLogger(__name__)


@dataclass
class MirroringSettings:
    """SAA-80/81: User-configurable mirroring parameters (persisted via API)."""

    enabled: bool = True
    max_rate_delta: float = 0.20         # SAA-75: ±20 % speaking-rate bound
    max_pitch_semitones: float = 2.0     # SAA-75: ±2 semitones pitch bound
    kill_switch_rapport_threshold: float = 0.50   # SAA-76
    smoothing_alpha: float = 0.30        # SAA-71: EMA weight
    calibration_turns: int = 2           # SAA-72: turns before baseline is locked
    baseline_drift_alpha: float = 0.04  # how fast baseline drifts after lock (0 = frozen)
    rapport_rate_weight: bool = True    # scale rate delta by rapport level


@dataclass
class MirroringDecision:
    """Result of one policy evaluation — passed to the TTS synthesiser."""

    applied: bool
    speaking_rate: float   # 1.0 = default tempo
    pitch: float           # semitones delta from neutral (0.0)
    reason: str            # SAA-77: human-readable explanation, emitted to log

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "speaking_rate": self.speaking_rate,
            "pitch": self.pitch,
            "reason": self.reason,
        }


_NEUTRAL = MirroringDecision(applied=False, speaking_rate=1.0, pitch=0.0, reason="")


class MirroringPolicy:
    """Compute bounded TTS adjustments that mirror the caller's vocal style.

    The policy is *stateless* — it reads from the session's
    :class:`~app.voice.mirroring.calibration.SessionCalibration` and the
    mutable :class:`MirroringSettings` object passed at construction time.
    Updating ``settings`` fields in place is thread-safe for single-process
    deployments (the GIL serialises attribute writes).
    """

    def __init__(self, settings: MirroringSettings | None = None) -> None:
        self.settings = settings or MirroringSettings()

    def compute(
        self,
        calibration: SessionCalibration,
        current_rapport: float,
    ) -> MirroringDecision:
        """Return the TTS adjustments for the next synthesis call.

        Parameters
        ----------
        calibration:
            The per-session calibration state (updated each turn before
            this method is called).
        current_rapport:
            Running average STT confidence for the session (0.0 – 1.0).
        """
        s = self.settings

        if not s.enabled:
            decision = MirroringDecision(False, 1.0, 0.0, "disabled")
            logger.debug("mirroring: %s", decision.reason)
            return decision

        # SAA-76: kill switch — rapport too low
        if current_rapport < s.kill_switch_rapport_threshold:
            decision = MirroringDecision(
                False, 1.0, 0.0,
                f"kill_switch(rapport={current_rapport:.2f}<threshold={s.kill_switch_rapport_threshold})",
            )
            logger.info("mirroring kill switch triggered: %s", decision.reason)
            return decision

        if not calibration.is_calibrated or calibration.smoothed is None:
            decision = MirroringDecision(False, 1.0, 0.0, "calibrating")
            logger.debug("mirroring: %s", decision.reason)
            return decision

        feat = calibration.smoothed
        baseline = calibration.baseline

        # --- Rate adjustment (SAA-75 bound) ---
        if baseline and baseline.speaking_rate_wpm > 0:
            raw_ratio = feat.speaking_rate_wpm / baseline.speaking_rate_wpm
            raw_delta = raw_ratio - 1.0

            # Improvement: scale aggressiveness by rapport level.
            # High rapport (>0.85) → full mirror; mid (0.65-0.85) → 60%;
            # low-ish (threshold..0.65) → gentle slow-down bias (−0.05).
            if s.rapport_rate_weight:
                if current_rapport >= 0.85:
                    rapport_scale = 1.0
                elif current_rapport >= 0.65:
                    rapport_scale = 0.6
                else:
                    raw_delta = -0.05  # nudge slower to de-escalate
                    rapport_scale = 1.0
            else:
                rapport_scale = 1.0

            rate_delta = max(-s.max_rate_delta, min(s.max_rate_delta, raw_delta * rapport_scale))
        else:
            rate_delta = 0.0
        speaking_rate = round(1.0 + rate_delta, 4)

        # --- Pitch adjustment (SAA-75 bound) ---
        # Improvement: combine confidence-based pitch_relative with hesitation penalty.
        # High hesitation → lower pitch regardless of confidence (grounds the voice).
        # Coefficient 1.0: 30% smoothed hesitation drops pitch by ~0.30 st — noticeable but gentle
        hesitation_penalty = feat.hesitation_rate * 1.0
        adjusted_pitch_relative = feat.pitch_relative - hesitation_penalty
        pitch = adjusted_pitch_relative * s.max_pitch_semitones
        pitch = round(max(-s.max_pitch_semitones, min(s.max_pitch_semitones, pitch)), 4)

        decision = MirroringDecision(
            True,
            speaking_rate,
            pitch,
            (
                f"mirroring("
                f"rate={speaking_rate:.3f},"
                f"pitch={pitch:+.2f}st,"
                f"rapport={current_rapport:.2f},"
                f"wpm={feat.speaking_rate_wpm:.0f},"
                f"hesit={feat.hesitation_rate:.2f})"
            ),
        )
        logger.info("mirroring applied: %s", decision.reason)   # SAA-77
        return decision

    def monitoring_flags(
        self,
        calibration: SessionCalibration,
        current_rapport: float,
    ) -> dict:
        """SAA-78: Return boolean/numeric flags for monitoring dashboards."""
        s = self.settings
        return {
            "mirroring_enabled": s.enabled,
            "calibrated": calibration.is_calibrated,
            "turns_observed": calibration.turns_observed,
            "calibration_turns_required": s.calibration_turns,
            "rapport_above_threshold": current_rapport >= s.kill_switch_rapport_threshold,
            "kill_switch_threshold": s.kill_switch_rapport_threshold,
            "max_rate_delta": s.max_rate_delta,
            "max_pitch_semitones": s.max_pitch_semitones,
        }
