"""SAA-68: TTS performance metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass
class TTSMetrics:
    """Per-synthesis timing and throughput data."""

    requested_at: float = field(default_factory=time)
    completed_at: float | None = None
    audio_duration_ms: float = 0.0
    character_count: int = 0
    voice_id: str = ""

    def record_completion(self, audio_duration_ms: float, char_count: int) -> None:
        self.completed_at = time()
        self.audio_duration_ms = audio_duration_ms
        self.character_count = char_count

    @property
    def synthesis_latency_ms(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.requested_at) * 1_000

    @property
    def chars_per_second(self) -> float | None:
        if self.synthesis_latency_ms is None or self.synthesis_latency_ms == 0:
            return None
        return self.character_count / (self.synthesis_latency_ms / 1_000)

    def to_dict(self) -> dict:
        return {
            "synthesis_latency_ms": self.synthesis_latency_ms,
            "audio_duration_ms": self.audio_duration_ms,
            "character_count": self.character_count,
            "chars_per_second": self.chars_per_second,
            "voice_id": self.voice_id,
        }


class TTSMetricsCollector:
    """Aggregate TTSMetrics across multiple synthesis calls in a session."""

    def __init__(self) -> None:
        self._records: list[TTSMetrics] = []

    def add(self, m: TTSMetrics) -> None:
        self._records.append(m)

    @property
    def total_synthesis_calls(self) -> int:
        return len(self._records)

    @property
    def average_latency_ms(self) -> float | None:
        latencies = [r.synthesis_latency_ms for r in self._records if r.synthesis_latency_ms is not None]
        return sum(latencies) / len(latencies) if latencies else None

    @property
    def total_audio_duration_ms(self) -> float:
        return sum(r.audio_duration_ms for r in self._records)

    @property
    def total_characters(self) -> int:
        return sum(r.character_count for r in self._records)

    def summary(self) -> dict:
        return {
            "total_synthesis_calls": self.total_synthesis_calls,
            "average_latency_ms": self.average_latency_ms,
            "total_audio_duration_ms": self.total_audio_duration_ms,
            "total_characters": self.total_characters,
        }
