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
    ) -> tuple[FSMContext, DialogueAction, str]:
        """Return (ctx, action, response_text) for the current fallback situation.

        ctx.state is updated in-place before returning so the pipeline always
        receives an FSMContext as the first element (not a DialogueState enum).
        """
        if ctx.retry_count >= self.config.max_retries:
            ctx.log("escalate", retry_count=ctx.retry_count)
            ctx.state = DialogueState.CLOSING
            return (
                ctx,
                DialogueAction.ESCALATE,
                self._escalation_text(),
            )

        ctx.retry_count += 1
        ctx.log("fallback", retry_count=ctx.retry_count)
        ctx.state = DialogueState.FALLBACK
        q = ctx.current_question
        text = custom_text or self._fallback_text(ctx.retry_count, q.question_type if q else None)
        return (
            ctx,
            DialogueAction.SPEAK_FALLBACK,
            text,
        )

    def build_confirmation_prompt(self, answer: str, question_prompt: str) -> str:
        return (
            f"I heard: {answer}. "
            f"Is that correct? Please say yes to confirm or no to try again."
        )

    def should_confirm(self, confidence: float) -> bool:
        """True when the answer confidence is good enough to attempt a confirmation."""
        return (
            self.config.low_confidence_threshold
            <= confidence
            < self.config.skip_confirmation_above
        )

    def should_auto_accept(self, confidence: float) -> bool:
        """True when we can accept the answer without confirmation."""
        return confidence >= self.config.skip_confirmation_above

    # -----------------------------------------------------------------------

    def _fallback_text(self, attempt: int, question_type: str | None = None) -> str:
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

    def _escalation_text(self) -> str:
        return (
            "I'm having difficulty understanding your responses. "
            "I'll connect you with a team member who can help. "
            "Thank you for your patience."
        )
