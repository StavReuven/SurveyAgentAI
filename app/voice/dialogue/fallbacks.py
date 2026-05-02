"""SAA-62: Fallback handling and confirmation prompts."""

from __future__ import annotations

from dataclasses import dataclass

from .fsm import DialogueAction, DialogueState, FSMContext


@dataclass
class FallbackConfig:
    max_retries: int = 3                 # attempts before escalation
    low_confidence_threshold: float = 0.60
    require_confirmation_above: float = 0.60  # confirm answers above this confidence
    skip_confirmation_above: float = 0.90     # auto-accept above this (no confirmation)


class FallbackHandler:
    """Decide what to do when the caller gives an unclear or repeated wrong response."""

    def __init__(self, config: FallbackConfig | None = None) -> None:
        self.config = config or FallbackConfig()

    def handle(
        self,
        ctx: FSMContext,
        custom_text: str | None = None,
        **_kwargs,
    ) -> tuple[FSMContext, DialogueAction, str]:
        if ctx.retry_count >= self.config.max_retries:
            ctx.log("escalate", retry_count=ctx.retry_count)
            ctx.state = DialogueState.CLOSING
            return (ctx, DialogueAction.ESCALATE, self._escalation_text(ctx))

        ctx.retry_count += 1
        ctx.log("fallback", retry_count=ctx.retry_count)
        ctx.state = DialogueState.FALLBACK
        q = ctx.current_question
        text = custom_text or self._fallback_text(ctx.retry_count, q.question_type if q else None, ctx)
        return (ctx, DialogueAction.SPEAK_FALLBACK, text)

    def build_confirmation_prompt(self, answer: str, question_prompt: str, ctx: FSMContext | None = None) -> str:
        if ctx and self._is_hebrew(ctx):
            return f"שמעתי: {answer}. האם זה נכון? אמור כן לאישור או לא לניסיון חוזר."
        return (
            f"I heard: {answer}. "
            f"Is that correct? Please say yes to confirm or no to try again."
        )

    def should_confirm(self, confidence: float) -> bool:
        return (
            self.config.low_confidence_threshold
            <= confidence
            < self.config.skip_confirmation_above
        )

    def should_auto_accept(self, confidence: float) -> bool:
        return confidence >= self.config.skip_confirmation_above

    # -----------------------------------------------------------------------

    def _is_hebrew(self, ctx: FSMContext) -> bool:
        lang = getattr(ctx, "_language", "en") or "en"
        return lang.startswith("he")

    def _fallback_text(self, attempt: int, question_type: str | None = None, ctx: FSMContext | None = None) -> str:
        if ctx and self._is_hebrew(ctx):
            hint = {
                "rating":    "אנא אמור מספר בין 1 ל-10.",
                "mcq":       "אנא אמור את האות של האפשרות שבחרת, למשל א, ב או ג.",
                "free_text": "אנא אמור את תשובתך בחופשיות.",
            }.get(question_type or "", "")
            base = [
                f"מצטער, זו אינה תשובה תקינה. {hint} אנא נסה שוב.",
                f"התשובה לא נקלטה כראוי. {hint} אנא הקשב ועשה ניסיון נוסף.",
                f"עדיין לא הצלחתי לקלוט תשובה תקינה. {hint} נסה פעם אחת נוספת.",
            ]
            return base[min(attempt - 1, len(base) - 1)]

        hint = {
            "rating":   "Please say a number between 1 and 10.",
            "mcq":      "Please say the letter of your chosen option, for example A, B, or C.",
            "free_text": "Please speak your answer freely.",
        }.get(question_type or "", "")
        base = [
            f"I'm sorry, that doesn't seem to be a valid answer. {hint} Could you please try again?",
            f"That answer isn't quite right. {hint} Please listen carefully and respond clearly.",
            f"I still couldn't get a valid answer. {hint} Please try once more.",
        ]
        return base[min(attempt - 1, len(base) - 1)]

    def _escalation_text(self, ctx: FSMContext | None = None) -> str:
        if ctx and self._is_hebrew(ctx):
            return (
                "אני מתקשה להבין את תשובותיך. "
                "אעביר אותך לנציג שיוכל לסייע. "
                "תודה על סבלנותך."
            )
        return (
            "I'm having difficulty understanding your responses. "
            "I'll connect you with a team member who can help. "
            "Thank you for your patience."
        )
