"""SAA-50: Voice AI Pipeline — orchestrates STT → NLU → Dialogue → TTS."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from time import time
from typing import AsyncIterator

from .agent.schema import AgentIntent
from .agent.service import AgentAIService
from .dialogue.fallbacks import FallbackConfig
from .dialogue.fsm import DialogueAction, FSMContext, QuestionContext
from .dialogue.transitions import DialogueManager
from .escalation import (
    EscalationConfig,
    EscalationSnapshot,
    compute_score,
    evaluate as evaluate_escalation,
    get_escalation_queue,
)
from .mirroring.features import FeatureExtractor
from .mirroring.policy import MirroringDecision, MirroringPolicy, MirroringSettings
from .nlu.classifier import RuleBasedClassifier
from .nlu.schema import IntentType
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
    mirroring_decision: MirroringDecision | None = None   # SAA-74
    agent_decision: dict | None = None                    # AgentAI structured output
    escalation_snapshot: EscalationSnapshot | None = None  # SAA-84


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
        mirroring_settings: MirroringSettings | None = None,
        agent_service: AgentAIService | None = None,
    ) -> None:
        self._config = config or PipelineConfig()
        self._stt: STTAdapter = stt_adapter or MockSTTAdapter(config=self._config.stt_config)
        self._tts: TTSAdapter = tts_adapter or MockTTSAdapter()
        self._nlu = RuleBasedClassifier()
        self._dm = DialogueManager(self._config.fallback_config)
        self._voice_selector = VoiceSelector()
        self._feature_extractor = FeatureExtractor()
        self._mirroring_policy = MirroringPolicy(mirroring_settings)
        self._agent: AgentAIService | None = agent_service
        self._escalation_config = EscalationConfig()

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
        campaign_name: str = "",
        campaign_description: str = "",
    ) -> FSMContext:
        session_id = str(uuid.uuid4())
        ctx = FSMContext(
            session_id=session_id,
            campaign_id=campaign_id,
            participant_phone=participant_phone,
            campaign_name=campaign_name,
            campaign_description=campaign_description,
            questions=questions,
            branch_rules=branch_rules,
        )
        ctx._language = language
        ctx._locale = locale
        # Sync calibration_turns from current policy settings (SAA-72)
        ctx.mirroring_calibration.calibration_turns = (
            self._mirroring_policy.settings.calibration_turns
        )
        return ctx

    async def start_session(self, ctx: FSMContext) -> TurnResult:
        """Greet the participant and ask the first question."""
        ctx, action, text = self._dm.start(ctx)
        ctx.log("bot_response", action=str(action), text=text)
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
            mirroring_decision=None,
        )

    async def process_turn(
        self,
        ctx: FSMContext,
        audio_chunks: AsyncIterator[bytes],
        hesitation_count: int = 0,
    ) -> TurnResult:
        """Process one caller audio turn through the full pipeline."""

        # --- STT ---
        stt_m = STTMetrics()
        transcript_stream = await self._stt.recognise(audio_chunks)
        final_text = transcript_stream.final.text if transcript_stream.final else ""
        confidence = transcript_stream.final.confidence if transcript_stream.final else 0.8
        duration_ms = transcript_stream.final.duration_ms if transcript_stream.final else 500.0
        if transcript_stream.final:
            stt_m.record_final(final_text)
        stt_m.audio_duration_ms = duration_ms

        # --- NLU / AgentAI ---
        q = ctx.current_question
        question_type = q.question_type if q else None
        language = getattr(ctx, "_language", "en") or "en"
        next_q = self._peek_next_question(ctx)

        agent_decision = None
        if self._agent:
            agent_decision = await self._agent.analyze_response(
                transcript=final_text,
                question=q,
                history=ctx.history,
                language=language,
                next_question=next_q,
                campaign_name=getattr(ctx, "campaign_name", ""),
                campaign_description=getattr(ctx, "campaign_description", ""),
                all_questions=ctx.questions,
            )
            intent = agent_decision.to_nlu_intent()
        else:
            nlu_result = self._nlu.classify(final_text, question_type=question_type)
            intent = nlu_result.primary

        # Log caller transcript (include confidence for rapport + mirroring)
        ctx.log("caller_input", text=final_text, confidence=confidence)
        # Tag intent on the caller_input entry NOW so _count_unclear_streak can find it.
        # Must happen before _dm.process() appends more history entries.
        if agent_decision:
            ctx.history[-1]["agent_intent"] = agent_decision.intent.value
        elif intent.intent_type == IntentType.UNKNOWN:
            ctx.history[-1]["agent_intent"] = AgentIntent.UNCLEAR.value

        # --- Dialogue ---
        ctx, action, response_text = self._dm.process(ctx, intent)

        # If the agent explicitly chose to escalate, override the FSM action so
        # the session is properly closed and the escalation snapshot is surfaced.
        if agent_decision and agent_decision.intent == AgentIntent.ESCALATE:
            action = DialogueAction.ESCALATE

        # Response text merging:
        # - SPEAK_QUESTION + agent has a full transition → use agent text only
        #   (agent already acknowledged the answer AND introduced the next question)
        # - SPEAK_QUESTION + agent only has short ack → prepend to FSM question text
        # - Any other action → use agent text (errors, clarification, opt-out, etc.)
        if agent_decision and agent_decision.response_text:
            if action == DialogueAction.SPEAK_QUESTION:
                # If agent built a full transition (includes next question intro), skip FSM text
                if next_q and len(agent_decision.response_text) > 40:
                    response_text = agent_decision.response_text
                else:
                    response_text = f"{agent_decision.response_text} {response_text}"
            else:
                response_text = agent_decision.response_text
        ctx.log("bot_response", action=str(action), text=response_text)

        # --- SAA-70: Feature extraction + calibration ---
        # Use a separate text for feature extraction so client-detected hesitations
        # (mic silence gaps) are counted without polluting the NLU/answer transcript.
        feat_text = final_text
        if hesitation_count > 0:
            feat_text = ' '.join(['um'] * min(hesitation_count, 6)) + ' ' + final_text
        features = self._feature_extractor.extract(
            text=feat_text,
            duration_ms=duration_ms,
            confidence=confidence,
        )
        ctx.mirroring_calibration.update(
            features,
            self._feature_extractor,
            alpha=self._mirroring_policy.settings.smoothing_alpha,
            baseline_drift_alpha=self._mirroring_policy.settings.baseline_drift_alpha,
        )

        # --- SAA-74: Mirroring policy ---
        rapport = self._compute_rapport(ctx)
        mirroring = self._mirroring_policy.compute(ctx.mirroring_calibration, rapport)

        # --- SAA-84: Escalation triggers ---
        escalation_snapshot: EscalationSnapshot | None = None
        escalation_reason = evaluate_escalation(ctx, agent_decision, rapport, self._escalation_config)
        if escalation_reason is not None:
            q = ctx.current_question
            cal = ctx.mirroring_calibration
            hesitation = cal.smoothed.hesitation_rate if cal.smoothed else 0.0
            urgency = compute_score(
                escalation_reason,
                rapport=rapport,
                hesitation_rate=hesitation,
                answers_completed=len(ctx.answers),
                total_questions=len(ctx.questions) or 1,
            )
            escalation_snapshot = EscalationSnapshot(
                session_id=ctx.session_id,
                campaign_id=ctx.campaign_id,
                participant_phone=ctx.participant_phone,
                reason=escalation_reason,
                current_question_key=q.question_key if q else None,
                current_question_prompt=q.prompt if q else None,
                answers_so_far=dict(ctx.answers),
                history=list(ctx.history),
                mirroring_snapshot=cal.to_dict(),
                rapport_score=rapport,
                urgency_score=urgency,
            )
            get_escalation_queue().push(escalation_snapshot)

        # --- TTS (with mirroring adjustments) ---
        tts_m = TTSMetrics()
        audio = await self._synthesise(response_text, ctx, mirroring=mirroring)
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
            mirroring_decision=mirroring,
            agent_decision=agent_decision.to_dict() if agent_decision else None,
            escalation_snapshot=escalation_snapshot,
        )

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _peek_next_question(ctx: FSMContext) -> QuestionContext | None:
        """Return the question after the current one without advancing the FSM."""
        questions = ctx.questions
        current = ctx.current_question
        if not current or not questions:
            return None
        for i, q in enumerate(questions):
            if q.question_key == current.question_key:
                return questions[i + 1] if i + 1 < len(questions) else None
        return None

    def _compute_rapport(self, ctx: FSMContext) -> float:
        """Average STT confidence across all caller turns in this session."""
        confidences = [
            e["confidence"]
            for e in ctx.history
            if e.get("event") == "caller_input" and isinstance(e.get("confidence"), float) and e["confidence"] > 0
        ]
        return round(sum(confidences) / len(confidences), 4) if confidences else 0.8

    async def _synthesise(
        self,
        text: str,
        ctx: FSMContext,
        mirroring: MirroringDecision | None = None,
    ) -> AudioData:
        language = getattr(ctx, "_language", "en")
        locale = getattr(ctx, "_locale", None)
        voice = self._voice_selector.select(language, locale, self._config.default_voice_gender)
        request = TTSRequest(
            text=text,
            voice_id=voice.id,
            language=voice.language,
            speaking_rate=mirroring.speaking_rate if (mirroring and mirroring.applied) else 1.0,
            pitch=mirroring.pitch if (mirroring and mirroring.applied) else 0.0,
        )
        return await self._tts.synthesize(request)
