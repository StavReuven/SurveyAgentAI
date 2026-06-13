"""AgentAIService — Claude-backed survey interview agent with rule-based fallback.

Usage:
    service = AgentAIService()          # reads ANTHROPIC_API_KEY from env
    decision = await service.analyze_response(
        transcript="pretty good, maybe four",
        question=question_ctx,
        history=ctx.history[-8:],
        language="en",
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

from .fallback import RuleBasedFallback
from .prompt import SYSTEM_PROMPT
from .schema import AgentDecision, AgentIntent, ExtractedAnswer, NextAction

if TYPE_CHECKING:
    from app.voice.dialogue.fsm import QuestionContext

logger = logging.getLogger(__name__)


# ── context builder ───────────────────────────────────────────────────────────

def _time_context() -> str:
    """Return a short human-readable time context string."""
    now = datetime.now()
    day = now.strftime("%A")          # e.g. "Thursday"
    hour = now.hour
    if 6 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 18:
        period = "afternoon"
    elif 18 <= hour < 23:
        period = "evening"
    else:
        period = "night"
    return f"It is currently {day} {period}."


def _build_user_message(
    transcript: str,
    question: QuestionContext | None,
    history: list[dict],
    language: str,
    next_question: QuestionContext | None = None,
) -> str:
    lines: list[str] = [
        f"Survey language: {language}",
        f"Context: {_time_context()}",
    ]

    if question:
        lines.append("\nCurrent question (being answered now):")
        lines.append(f"  id:   {question.question_key}")
        lines.append(f"  text: {question.prompt}")
        lines.append(f"  type: {question.question_type}")
        cfg = question.config or {}
        if cfg.get("options"):
            lines.append(f"  options: {cfg['options']}")
        if cfg.get("min") is not None:
            lines.append(f"  range: {cfg['min']}–{cfg.get('max', 10)}")
    else:
        lines.append("\nNo active question (greeting phase).")

    if next_question:
        lines.append("\nNext question (to introduce if answer is accepted):")
        lines.append(f"  text: {next_question.prompt}")
        lines.append(f"  type: {next_question.question_type}")
        cfg2 = next_question.config or {}
        if cfg2.get("options"):
            lines.append(f"  options: {cfg2['options']}")
        if cfg2.get("min") is not None:
            lines.append(f"  range: {cfg2['min']}–{cfg2.get('max', 10)}")

    recent = history[-8:] if len(history) > 8 else history
    if recent:
        lines.append("\nRecent conversation:")
        for e in recent:
            role = e.get("event", "")
            text = e.get("text", "")
            if role == "caller_input" and text:
                lines.append(f"  Respondent: {text}")
            elif role == "bot_response" and text:
                lines.append(f"  Agent: {text}")

    lines.append(f'\nRespondent\'s latest answer: "{transcript}"')
    lines.append("\nAnalyse this answer and return your decision as JSON.")
    return "\n".join(lines)


# ── main service ──────────────────────────────────────────────────────────────

class AgentAIService:
    """LLM-powered survey dialogue agent.

    If ANTHROPIC_API_KEY is not set or the anthropic package is missing,
    every call transparently falls back to the deterministic rule-based agent.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._model = model
        self._fallback = RuleBasedFallback()
        self._client = None

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=key)
                logger.info("AgentAI: Claude client ready (model=%s)", model)
            except ImportError:
                logger.warning(
                    "AgentAI: 'anthropic' package not installed — "
                    "run 'pip install anthropic' to enable LLM mode. "
                    "Using rule-based fallback."
                )
        else:
            logger.info("AgentAI: ANTHROPIC_API_KEY not set — using rule-based fallback")

    @property
    def llm_available(self) -> bool:
        return self._client is not None

    # ── public API ────────────────────────────────────────────────────────────

    async def analyze_response(
        self,
        transcript: str,
        question: QuestionContext | None,
        history: list[dict],
        language: str = "en",
        next_question: QuestionContext | None = None,
    ) -> AgentDecision:
        """Analyse a respondent's answer; return a structured AgentDecision."""
        if not transcript.strip():
            he = language.startswith("he")
            return AgentDecision(
                intent=AgentIntent.UNCLEAR,
                confidence=0.20,
                next_action=NextAction.REPEAT,
                response_text=(
                    "לא שמעתי כלום. תוכל לחזור בבקשה?" if he
                    else "I didn't hear anything. Could you please repeat?"
                ),
                should_save_answer=False,
            )

        if self._client:
            try:
                return await self._call_llm(transcript, question, history, language, next_question)
            except Exception as exc:
                logger.warning(
                    "AgentAI: LLM call failed (%s) — switching to rule-based fallback", exc
                )

        return self._fallback.analyze(transcript, question, language, next_question)

    def normalize_answer(
        self,
        transcript: str,
        question: QuestionContext | None,
        language: str = "en",
    ) -> ExtractedAnswer | None:
        """Extract and normalise an answer without a full dialogue decision."""
        decision = self._fallback.analyze(transcript, question, language)
        return decision.extracted_answer if decision.should_save_answer else None

    def apply_skip_logic(
        self,
        answered_key: str,
        answered_value: str,
        branch_rules: list,
        questions: list,
    ) -> int | None:
        """Evaluate branch rules and return the next question index (or None for linear)."""
        for rule in sorted(branch_rules, key=lambda r: getattr(r, "priority", 0)):
            if rule.source_question_id is None:
                continue
            if not self._rule_matches(rule, answered_value):
                continue
            if getattr(rule, "action", None) in ("end", "escalate"):
                return -1
            if getattr(rule, "action", None) == "goto":
                target_id = getattr(rule, "target_question_id", None)
                if target_id is not None:
                    for idx, q in enumerate(questions):
                        if q.question_id == target_id:
                            return idx
        return None

    # ── LLM path ─────────────────────────────────────────────────────────────

    async def _call_llm(
        self,
        transcript: str,
        question: QuestionContext | None,
        history: list[dict],
        language: str,
        next_question: QuestionContext | None = None,
    ) -> AgentDecision:
        user_msg = _build_user_message(transcript, question, history, language, next_question)
        loop = asyncio.get_running_loop()
        message = await loop.run_in_executor(
            None,
            lambda: self._client.messages.create(
                model=self._model,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            ),
        )
        raw = message.content[0].text.strip()
        return self._parse_decision(raw, transcript, language)

    def _parse_decision(
        self, raw: str, transcript: str, language: str = "en"
    ) -> AgentDecision:
        # Strip markdown fences that some models add
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) >= 2 else raw

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("AgentAI: unparseable LLM output — falling back. Preview: %r", raw[:200])
            return self._fallback.analyze(transcript, None, language)

        try:
            intent = AgentIntent(data["intent"])
        except (KeyError, ValueError):
            intent = AgentIntent.UNCLEAR

        try:
            next_action = NextAction(data["next_action"])
        except (KeyError, ValueError):
            next_action = NextAction.ASK_CLARIFICATION

        ea_raw = data.get("extracted_answer")
        extracted: ExtractedAnswer | None = None
        if ea_raw and ea_raw.get("value") is not None:
            extracted = ExtractedAnswer(
                value=ea_raw["value"],
                type=ea_raw.get("type", "free_text"),
                raw_text=ea_raw.get("raw_text", transcript),
            )

        return AgentDecision(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            next_action=next_action,
            response_text=str(data.get("response_text", "")),
            should_save_answer=bool(data.get("should_save_answer", False)),
            extracted_answer=extracted,
            next_question_id=data.get("next_question_id"),
            reason=str(data.get("reason", "")),
        )

    # ── helper ────────────────────────────────────────────────────────────────

    @staticmethod
    def _rule_matches(rule: object, answer: str) -> bool:
        v = str(getattr(rule, "value", ""))
        op = getattr(rule, "operator", "equals")
        if op == "equals":      return answer.lower() == v.lower()
        if op == "not_equals":  return answer.lower() != v.lower()
        if op == "contains":    return v.lower() in answer.lower()
        if op == "gt":
            try:    return float(answer) > float(v)
            except ValueError: return False
        if op == "lt":
            try:    return float(answer) < float(v)
            except ValueError: return False
        return False
