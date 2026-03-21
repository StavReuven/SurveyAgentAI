"""SAA-52: STT adapter — abstract interface + mock implementation."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from .metrics import STTMetrics
from .transcript import TranscriptEvent, TranscriptStream, TranscriptType


@dataclass
class STTConfig:
    language: str = "en-US"
    sample_rate_hz: int = 16_000
    encoding: str = "LINEAR16"
    interim_results: bool = True       # stream partials
    confidence_threshold: float = 0.6  # below this → low-confidence flag


class STTAdapter(ABC):
    """Abstract base class for Speech-to-Text providers.

    Concrete implementations (Google, AWS, Azure, Deepgram …) must subclass
    this and implement `stream`.
    """

    def __init__(self, config: STTConfig | None = None) -> None:
        self.config = config or STTConfig()

    @abstractmethod
    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptEvent]:
        """Yield TranscriptEvents (partial then final) as audio arrives."""
        ...

    async def recognise(self, audio_chunks: AsyncIterator[bytes]) -> TranscriptStream:
        """Convenience wrapper: collect all events into a TranscriptStream."""
        metrics = STTMetrics()
        stream = TranscriptStream()
        async for event in self.stream(audio_chunks):
            if not event.is_final:
                metrics.record_first_partial()
            else:
                metrics.record_final(event.text)
            stream.push(event)
        return stream


# ---------------------------------------------------------------------------
# Mock adapter (used for tests and local development without a real STT service)
# ---------------------------------------------------------------------------

class MockSTTAdapter(STTAdapter):
    """Deterministic mock that returns preset responses for testing.

    Usage::

        adapter = MockSTTAdapter(responses=["I want to skip", "repeat please"])
        stream = await adapter.recognise(fake_audio_iter())
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        config: STTConfig | None = None,
        confidence: float = 0.92,
    ) -> None:
        super().__init__(config)
        self._responses: list[str] = responses or ["okay"]
        self._confidence = confidence
        self._index = 0

    async def stream(
        self,
        audio_chunks: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptEvent]:
        # Consume audio (discard — mock doesn't need it)
        async for _ in audio_chunks:
            await asyncio.sleep(0)

        text = self._responses[self._index % len(self._responses)]
        self._index += 1

        # Emit a partial first, then a final
        partial_text = text[: max(1, len(text) // 2)]
        yield TranscriptEvent(
            transcript_type=TranscriptType.PARTIAL,
            text=partial_text,
            confidence=-1.0,
        )
        await asyncio.sleep(0.01)  # simulate streaming delay
        yield TranscriptEvent(
            transcript_type=TranscriptType.FINAL,
            text=text,
            confidence=self._confidence,
            duration_ms=len(text) * 60.0,  # rough approximation
        )
