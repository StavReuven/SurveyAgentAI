"""SAA-54: STT latency metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass
class STTMetrics:
    """Timing measurements for a single STT recognition turn."""

    audio_start: float = field(default_factory=time)
    first_partial_at: float | None = None   # wall-clock when first partial arrived
    final_at: float | None = None           # wall-clock when final transcript arrived
    audio_duration_ms: float = 0.0          # length of audio that was recognised
    characters_recognised: int = 0

    def record_first_partial(self) -> None:
        if self.first_partial_at is None:
            self.first_partial_at = time()

    def record_final(self, text: str) -> None:
        self.final_at = time()
        self.characters_recognised = len(text)

    # ---- derived metrics ------------------------------------------------

    @property
    def time_to_first_word_ms(self) -> float | None:
        """Wall-clock ms from audio start to first partial transcript."""
        if self.first_partial_at is None:
            return None
        return (self.first_partial_at - self.audio_start) * 1_000

    @property
    def total_processing_ms(self) -> float | None:
        """Wall-clock ms from audio start to final transcript."""
        if self.final_at is None:
            return None
        return (self.final_at - self.audio_start) * 1_000

    @property
    def realtime_factor(self) -> float | None:
        """processing_time / audio_duration  (<1.0 means faster than real-time)."""
        if self.total_processing_ms is None or self.audio_duration_ms == 0:
            return None
        return self.total_processing_ms / self.audio_duration_ms

    def to_dict(self) -> dict:
        return {
            "time_to_first_word_ms": self.time_to_first_word_ms,
            "total_processing_ms": self.total_processing_ms,
            "audio_duration_ms": self.audio_duration_ms,
            "realtime_factor": self.realtime_factor,
            "characters_recognised": self.characters_recognised,
        }
