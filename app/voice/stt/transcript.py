"""SAA-53: Partial/final transcript data structures for streaming STT."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time


class TranscriptType(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"


@dataclass
class TranscriptEvent:
    """Single event emitted by the STT stream."""

    transcript_type: TranscriptType
    text: str
    confidence: float          # 0.0–1.0; -1.0 when unavailable (partials)
    timestamp: float = field(default_factory=time)
    duration_ms: float = 0.0   # audio duration this transcript covers

    @property
    def is_final(self) -> bool:
        return self.transcript_type == TranscriptType.FINAL

    @property
    def is_low_confidence(self) -> bool:
        """True when we have a real confidence score and it is below threshold."""
        return 0.0 <= self.confidence < 0.6


@dataclass
class TranscriptStream:
    """Manages the sequence of partial events that culminate in a final transcript."""

    partials: list[TranscriptEvent] = field(default_factory=list)
    final: TranscriptEvent | None = None

    def push(self, event: TranscriptEvent) -> None:
        if event.is_final:
            self.final = event
        else:
            self.partials.append(event)

    @property
    def latest_text(self) -> str:
        if self.final:
            return self.final.text
        if self.partials:
            return self.partials[-1].text
        return ""

    @property
    def is_complete(self) -> bool:
        return self.final is not None

    def reset(self) -> None:
        self.partials.clear()
        self.final = None
