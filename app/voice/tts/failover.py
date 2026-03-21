"""SAA-67: Failover TTS — tries primary adapter, falls back to secondary on error."""

from __future__ import annotations

import logging

from .adapter import AudioData, TTSAdapter, TTSRequest

logger = logging.getLogger(__name__)


class FailoverTTSAdapter(TTSAdapter):
    """Wraps a primary and one or more fallback TTS adapters.

    On any exception from the primary, the next adapter in the chain is tried.
    If all fail, the last exception is re-raised.
    """

    def __init__(self, primary: TTSAdapter, *fallbacks: TTSAdapter) -> None:
        if not fallbacks:
            raise ValueError("At least one fallback adapter must be provided.")
        self._chain: list[TTSAdapter] = [primary, *fallbacks]

    async def synthesize(self, request: TTSRequest) -> AudioData:
        last_exc: Exception | None = None
        for idx, adapter in enumerate(self._chain):
            try:
                audio = await adapter.synthesize(request)
                if idx > 0:
                    logger.warning(
                        "TTS failover: primary failed, used fallback #%d (%s)",
                        idx,
                        type(adapter).__name__,
                    )
                return audio
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "TTS adapter %s failed: %s", type(adapter).__name__, exc
                )
                last_exc = exc

        raise RuntimeError("All TTS adapters failed") from last_exc
