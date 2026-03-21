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

    def handle(self, ctx: FSMContext) -> tuple[DialogueState, DialogueAction, str]:
        """Return (next_state, action, response_text) for the current fallback situation."""
        if ctx.retry_count >= self.config.max_retries:
            ctx.log("escalate", retry_count=ctx.retry_count)
            return (
                DialogueState.CLOSING,
                DialogueAction.ESCALATE,
                self._escalation_text(),
            )

        ctx.retry_count += 1
        ctx.log("fallback", retry_count=ctx.retry_count)
        return (
            DialogueState.FALLBACK,
            DialogueAction.SPEAK_FALLBACK,
            self._fallback_text(ctx.retry_count),
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

    def _fallback_text(self, attempt: int) -> str:
        messages = [
            "I'm sorry, I didn't quite get that. Could you please try again?",
            "I'm still having trouble understanding. Please say your answer clearly.",
            "Let me try one more time. Please respond clearly when you're ready.",
        ]
        return messages[min(attempt - 1, len(messages) - 1)]

    def _escalation_text(self) -> str:
        return (
            "I'm having difficulty understanding your responses. "
            "I'll connect you with a team member who can help. "
            "Thank you for your patience."
        )
