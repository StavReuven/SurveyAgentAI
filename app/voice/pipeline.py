"""SAA-50: Voice AI Pipeline — orchestrates STT → NLU → Dialogue → TTS."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from time import time
from typing import AsyncIterator

from .dialogue.fallbacks import FallbackConfig
from .dialogue.fsm import DialogueAction, FSMContext, QuestionContext
from .dialogue.transitions import DialogueManager
from .nlu.classifier import RuleBasedClassifier
from .stt.adapter import MockSTTAdapter, STTAdapter, STTConfig
from .stt.metrics import STTMetrics
from .tts.adapter import AudioData, MockTTSAdapter, TTSAdapter, TTSRequest
from .tts.metrics import TTSMetrics, TTSMetricsCollector
from .tts.voice_selection import VoiceSelector


@dataclass
class TurnResult:
    """Output from processing one audio turn through the pipeline."""

    session_id: str
    response_text: str
    response_audio: AudioData
    dialogue_action: DialogueAction
    current_state: str
    current_question_key: str | None
    stt_metrics: dict
    tts_metrics: dict
    session_complete: bool


@dataclass
class PipelineConfig:
    stt_config: STTConfig = field(default_factory=STTConfig)
    fallback_config: FallbackConfig = field(default_factory=FallbackConfig)
    default_voice_gender: str = "female"


class VoicePipeline:
    """End-to-end Voice AI Pipeline for survey calls.

    Usage::

        pipeline = VoicePipeline()
        ctx = await pipeline.start_session(campaign_id=1, questions=..., rules=..., phone=...)
        result = await pipeline.process_turn(ctx, audio_chunks_iter)
    """

    def __init__(
        self,
        stt_adapter: STTAdapter | None = None,
        tts_adapter: TTSAdapter | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        self._config = config or PipelineConfig()
        self._stt: STTAdapter = stt_adapter or MockSTTAdapter(config=self._config.stt_config)
        self._tts: TTSAdapter = tts_adapter or MockTTSAdapter()
        self._nlu = RuleBasedClassifier()
        self._dm = DialogueManager(self._config.fallback_config)
        self._voice_selector = VoiceSelector()

    # -----------------------------------------------------------------------
    # Session lifecycle
    # -----------------------------------------------------------------------

    def create_session(
        self,
        campaign_id: int,
        participant_phone: str,
        questions: list[QuestionContext],
        branch_rules: list,
        language: str = "en",
        locale: str | None = None,
    ) -> FSMContext:
        session_id = str(uuid.uuid4())
        ctx = FSMContext(
            session_id=session_id,
            campaign_id=campaign_id,
            participant_phone=participant_phone,
            questions=questions,
            branch_rules=branch_rules,
        )
        ctx._language = language
        ctx._locale = locale
        return ctx

    async def start_session(self, ctx: FSMContext) -> TurnResult:
        """Greet the participant and ask the first question."""
        ctx, action, text = self._dm.start(ctx)
        audio = await self._synthesise(text, ctx)
        return TurnResult(
            session_id=ctx.session_id,
            response_text=text,
            response_audio=audio,
            dialogue_action=action,
            current_state=ctx.state,
            current_question_key=(ctx.current_question.question_key if ctx.current_question else None),
            stt_metrics={},
            tts_metrics={},
            session_complete=False,
        )

    async def process_turn(
        self,
        ctx: FSMContext,
        audio_chunks: AsyncIterator[bytes],
    ) -> TurnResult:
        """Process one caller audio turn through the full pipeline."""

        # --- STT ---
        stt_m = STTMetrics()
        transcript_stream = await self._stt.recognise(audio_chunks)
        final_text = transcript_stream.final.text if transcript_stream.final else ""
        if transcript_stream.final:
            stt_m.record_final(final_text)
        stt_m.audio_duration_ms = transcript_stream.final.duration_ms if transcript_stream.final else 0.0

        # --- NLU ---
        q = ctx.current_question
        question_type = q.question_type if q else None
        nlu_result = self._nlu.classify(final_text, question_type=question_type)
        intent = nlu_result.primary

        # --- Dialogue ---
        ctx, action, response_text = self._dm.process(ctx, intent)

        # --- TTS ---
        tts_m = TTSMetrics()
        audio = await self._synthesise(response_text, ctx)
        tts_m.record_completion(audio.duration_ms, len(response_text))

        session_complete = action in (
            DialogueAction.END_CALL,
            DialogueAction.SPEAK_CLOSING,
            DialogueAction.ESCALATE,
        )

        return TurnResult(
            session_id=ctx.session_id,
            response_text=response_text,
            response_audio=audio,
            dialogue_action=action,
            current_state=ctx.state,
            current_question_key=(ctx.current_question.question_key if ctx.current_question else None),
            stt_metrics=stt_m.to_dict(),
            tts_metrics=tts_m.to_dict(),
            session_complete=session_complete,
        )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _synthesise(self, text: str, ctx: FSMContext) -> AudioData:
        language = getattr(ctx, "_language", "en")
        locale = getattr(ctx, "_locale", None)
        voice = self._voice_selector.select(language, locale, self._config.default_voice_gender)
        request = TTSRequest(
            text=text,
            voice_id=voice.id,
            language=voice.language,
        )
        return await self._tts.synthesize(request)
