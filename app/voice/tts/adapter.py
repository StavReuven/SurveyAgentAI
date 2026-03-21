"""SAA-65: TTS adapter — abstract interface + mock implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AudioData:
    """Raw audio produced by a TTS synthesis call."""

    audio_bytes: bytes
    format: str = "wav"         # "wav" | "mp3" | "ogg"
    sample_rate_hz: int = 22_050
    duration_ms: float = 0.0
    voice_id: str = ""
    text: str = ""


@dataclass
class TTSRequest:
    text: str
    voice_id: str
    language: str = "en-US"
    speaking_rate: float = 1.0   # 0.5–2.0
    pitch: float = 0.0           # semitones relative to default
    audio_format: str = "wav"


class TTSAdapter(ABC):
    """Abstract base class for Text-to-Speech providers.

    Subclass this for Google Cloud TTS, AWS Polly, Azure TTS, ElevenLabs, etc.
    """

    @abstractmethod
    async def synthesize(self, request: TTSRequest) -> AudioData:
        """Convert text to speech audio."""
        ...

    async def synthesize_text(
        self,
        text: str,
        voice_id: str,
        language: str = "en-US",
    ) -> AudioData:
        """Convenience wrapper with minimal parameters."""
        return await self.synthesize(TTSRequest(text=text, voice_id=voice_id, language=language))


# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------

class MockTTSAdapter(TTSAdapter):
    """Returns silent placeholder audio for development and testing.

    Audio is a minimal valid WAV file (44 bytes header, no samples).
    """

    _SILENT_WAV_HEADER = (
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        b"\x01\x00\x01\x00\x80\xbb\x00\x00\x00w\x01\x00"
        b"\x02\x00\x10\x00data\x00\x00\x00\x00"
    )

    async def synthesize(self, request: TTSRequest) -> AudioData:
        # Estimate duration: ~150 words per minute, ~5 chars per word
        words = max(1, len(request.text) // 5)
        duration_ms = (words / 150) * 60_000

        return AudioData(
            audio_bytes=self._SILENT_WAV_HEADER,
            format="wav",
            sample_rate_hz=48_000,
            duration_ms=duration_ms,
            voice_id=request.voice_id,
            text=request.text,
        )
