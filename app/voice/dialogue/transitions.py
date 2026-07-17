"""SAA-61: FSM transition logic — routes intents to next states and actions."""

from __future__ import annotations

import re

from .fallbacks import FallbackConfig, FallbackHandler
from .fsm import DialogueAction, DialogueState, FSMContext
from ..nlu.schema import Intent, IntentType

_ANY_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")


class DialogueManager:
    """Core FSM that processes one NLU intent per turn and returns a response.

    Integrates with the existing survey Question / BranchRule models via
    FSMContext.questions (loaded by the pipeline from the DB).
    """

    def __init__(self, fallback_config: FallbackConfig | None = None) -> None:
        self._fallback = FallbackHandler(fallback_config)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self, ctx: FSMContext) -> tuple[FSMContext, DialogueAction, str]:
        """Initialise a fresh session: greet and ask the first question."""
        ctx.state = DialogueState.ASKING
        ctx.log("session_start")
        response = self._greeting_text(ctx)
        if ctx.current_question:
            response += " " + ctx.current_question.prompt
        return ctx, DialogueAction.SPEAK_GREETING, response

    def process(
        self,
        ctx: FSMContext,
        intent: Intent,
    ) -> tuple[FSMContext, DialogueAction, str]:
        """Main transition: given current state + intent → new state + action + text."""
        ctx.log(
            "turn",
            state=ctx.state,
            intent=intent.intent_type,
            confidence=intent.confidence,
        )

        handler = self._DISPATCH.get(ctx.state)
        if handler is None:
            return ctx, DialogueAction.END_CALL, "Goodbye."

        return handler(self, ctx, intent)

    # -----------------------------------------------------------------------
    # Per-state handlers
    # -----------------------------------------------------------------------

    def _handle_waiting(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        q = ctx.current_question

        if intent.intent_type == IntentType.REPEAT:
            ctx.state = DialogueState.REPEATING
            text = q.prompt if q else ("אנא חזור על תשובתך." if self._is_hebrew(ctx) else "Could you please repeat your answer?")
            return ctx, DialogueAction.REPEAT_QUESTION, text

        if intent.intent_type == IntentType.REPHRASE:
            ctx.state = DialogueState.REPHRASING
            text = self._rephrase_text(q, ctx)
            return ctx, DialogueAction.SPEAK_REPHRASE, text

        if intent.intent_type in (IntentType.NOT_NOW, IntentType.SKIP):
            msg = "מובן. תודה על זמנך. להתראות!" if self._is_hebrew(ctx) else "Understood. Thank you for your time. Goodbye."
            return self._close(ctx, msg)

        if intent.intent_type == IntentType.HELP:
            text = self._help_text(q, ctx)
            return ctx, DialogueAction.SPEAK_QUESTION, text

        if intent.intent_type == IntentType.ANSWER:
            return self._handle_answer(ctx, intent)

        # For free_text questions "yes" / "no" are valid answers, not meta-intents
        if intent.intent_type in (IntentType.CONFIRM_YES, IntentType.CONFIRM_NO) and \
                q and q.question_type == "free_text":
            value = "yes" if intent.intent_type == IntentType.CONFIRM_YES else "no"
            synthetic = Intent(
                intent_type=IntentType.ANSWER,
                confidence=0.90,
                raw_text=intent.raw_text,
                extracted_value=value,
            )
            return self._handle_answer(ctx, synthetic)

        # Unknown / low confidence — use a context-aware hint when possible
        return self._fallback.handle(ctx, self._validation_hint(intent.raw_text or "", q, ctx))

    def _handle_repeating(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        ctx.state = DialogueState.WAITING
        return self._handle_waiting(ctx, intent)

    def _handle_rephrasing(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        ctx.state = DialogueState.WAITING
        return self._handle_waiting(ctx, intent)

    def _handle_fallback(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        ctx.state = DialogueState.WAITING
        return self._handle_waiting(ctx, intent)

    def _handle_confirming(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        if intent.intent_type == IntentType.CONFIRM_YES:
            return self._accept_pending_answer(ctx)

        if intent.intent_type == IntentType.CONFIRM_NO:
            ctx.pending_answer = None
            ctx.retry_count += 1
            ctx.state = DialogueState.WAITING
            q = ctx.current_question
            if self._is_hebrew(ctx):
                text = f"בסדר. {q.prompt}" if q else "בואו ננסה שוב."
            else:
                text = f"No problem. {q.prompt}" if q else "Let's try again."
            return ctx, DialogueAction.SPEAK_QUESTION, text

        # Unclear response during confirmation
        ctx.state = DialogueState.CONFIRMING
        text = "אמור כן לאישור או לא לניסיון חוזר." if self._is_hebrew(ctx) else "Please say yes to confirm or no to try again."
        return ctx, DialogueAction.CONFIRM_ANSWER, text

    def _handle_closing_done_error(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        return ctx, DialogueAction.END_CALL, "Goodbye."

    # -----------------------------------------------------------------------
    # Answer acceptance
    # -----------------------------------------------------------------------

    def _handle_answer(
        self, ctx: FSMContext, intent: Intent
    ) -> tuple[FSMContext, DialogueAction, str]:
        value = intent.extracted_value or intent.raw_text
        confidence = intent.confidence

        if self._fallback.should_auto_accept(confidence):
            ctx.pending_answer = value
            return self._accept_pending_answer(ctx)

        if self._fallback.should_confirm(confidence):
            ctx.pending_answer = value
            ctx.state = DialogueState.CONFIRMING
            q = ctx.current_question
            text = self._fallback.build_confirmation_prompt(
                value, q.prompt if q else "", ctx
            )
            return ctx, DialogueAction.CONFIRM_ANSWER, text

        # Below low-confidence threshold → treat as unclear
        return self._fallback.handle(ctx)

    def _accept_pending_answer(
        self, ctx: FSMContext
    ) -> tuple[FSMContext, DialogueAction, str]:
        q = ctx.current_question
        if q and ctx.pending_answer:
            # MCQ answers are captured as an option letter (A/B/C/D); store the
            # literal option text instead so answers/reports are readable
            # without cross-referencing the question's option order.
            stored_value = self._resolve_mcq_answer_text(q, ctx.pending_answer) or ctx.pending_answer
            ctx.answers[q.question_key] = stored_value
            ctx.log("answer_accepted", key=q.question_key, value=stored_value)

        # Apply branch rules (if any) — rule engine looks at current answer
        next_idx = self._evaluate_branch_rules(ctx)

        if next_idx == -1:  # "end" action
            return self._close(ctx, self._closing_text(ctx))

        ctx.advance_question(next_idx)
        next_q = ctx.current_question

        if next_q is None:
            return self._close(ctx, self._closing_text(ctx))

        ctx.state = DialogueState.ASKING
        return ctx, DialogueAction.SPEAK_QUESTION, next_q.prompt

    # -----------------------------------------------------------------------
    # Branch rule evaluation  (mirrors app/models.py BranchRule logic)
    # -----------------------------------------------------------------------

    def _evaluate_branch_rules(self, ctx: FSMContext) -> int | None:
        """Return next question index, -1 for end, or None for linear advance."""
        q = ctx.current_question
        if q is None:
            return None

        answer = ctx.answers.get(q.question_key, "")
        # MCQ answers are stored as the literal option text (resolved from the
        # captured option letter — see _accept_pending_answer), but a branch
        # rule might be authored against either the option text or a raw
        # letter (A/B/C/D). Check both forms so either authoring style matches.
        alt_answer = (
            self._resolve_mcq_answer_text(q, answer) or self._resolve_mcq_answer_letter(q, answer)
        )

        matching_rules = ctx._branch_rules_for_current(q.question_id)
        for rule in sorted(matching_rules, key=lambda r: r.priority):
            if self._rule_matches(rule, answer) or (
                alt_answer is not None and self._rule_matches(rule, alt_answer)
            ):
                if rule.action == "end":
                    return -1
                if rule.action == "escalate":
                    return -1
                if rule.action == "goto" and rule.target_question_id is not None:
                    # Find target index
                    for idx, qc in enumerate(ctx.questions):
                        if qc.question_id == rule.target_question_id:
                            return idx
        return None  # linear advance

    def _resolve_mcq_answer_text(self, q, answer: str) -> str | None:
        """For MCQ questions, map a stored option letter (A/B/C/D) back to its
        literal option text (e.g. 'B' -> 'ronaldo'), or None if not applicable."""
        if q.question_type != "mcq" or not answer or len(answer) != 1:
            return None
        letter = answer.upper()
        if letter not in "ABCD":
            return None
        options = (q.config or {}).get("options") or (q.config or {}).get("choices") or []
        idx = ord(letter) - ord("A")
        if 0 <= idx < len(options):
            return str(options[idx])
        return None

    def _resolve_mcq_answer_letter(self, q, answer: str) -> str | None:
        """Reverse of _resolve_mcq_answer_text: map a literal option text
        (e.g. 'ronaldo') back to its option letter ('B'), for branch rules
        authored against a raw letter instead of the option text."""
        if q.question_type != "mcq" or not answer:
            return None
        options = (q.config or {}).get("options") or (q.config or {}).get("choices") or []
        for i, opt in enumerate(options[:4]):
            if str(opt).lower() == answer.lower():
                return "ABCD"[i]
        return None

    def _rule_matches(self, rule: object, answer: str) -> bool:
        v = str(rule.value)
        if rule.operator == "equals":
            return answer.lower() == v.lower()
        if rule.operator == "not_equals":
            return answer.lower() != v.lower()
        if rule.operator == "contains":
            return v.lower() in answer.lower()
        if rule.operator == "gt":
            try:
                return float(answer) > float(v)
            except ValueError:
                return False
        if rule.operator == "lt":
            try:
                return float(answer) < float(v)
            except ValueError:
                return False
        return False

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _validation_hint(self, raw_text: str, q, ctx: FSMContext | None = None) -> str | None:
        if q is None:
            return None
        hebrew = ctx and self._is_hebrew(ctx)
        if q.question_type == "rating":
            m = _ANY_NUMBER_RE.search(raw_text)
            if m:
                num_str = m.group(1)
                try:
                    num = float(num_str)
                    if num < 1 or num > 10:
                        return (f"{num_str} מחוץ לטווח. אנא אמור מספר בין 1 ל-10." if hebrew
                                else f"{num_str} is out of range. Please say a number between 1 and 10.")
                except ValueError:
                    pass
            if raw_text.strip():
                return ("דירוג לא תקין. אנא אמור מספר בין 1 ל-10." if hebrew
                        else "That's not a valid rating. Please say a number between 1 and 10.")
            return None
        if q.question_type == "mcq":
            if raw_text.strip():
                return (f"לא קלטתי אפשרות תקינה. {q.prompt}" if hebrew
                        else f"I didn't catch a valid option. {q.prompt}")
            return None
        return None

    def _close(
        self, ctx: FSMContext, text: str
    ) -> tuple[FSMContext, DialogueAction, str]:
        ctx.state = DialogueState.DONE
        ctx.log("session_end")
        return ctx, DialogueAction.SPEAK_CLOSING, text

    def _is_hebrew(self, ctx: FSMContext) -> bool:
        lang = getattr(ctx, "_language", "en") or "en"
        return lang.startswith("he")

    def _greeting_text(self, ctx: FSMContext) -> str:
        if self._is_hebrew(ctx):
            return (
                "שלום! תודה שהקדשת מזמנך להשתתף בסקר שלנו היום. "
                "המשוב שלך חשוב לנו מאוד. "
                "נתחיל."
            )
        return (
            "Hello! Thank you for taking the time to participate in our survey today. "
            "Your feedback is very important to us. "
            "Let's get started."
        )

    def _closing_text(self, ctx: FSMContext | None = None) -> str:
        if ctx and self._is_hebrew(ctx):
            return (
                "זהו סיום הסקר. "
                "תודה רבה על תשובותיך. יום נפלא לך. להתראות!"
            )
        return (
            "That's the end of the survey. "
            "Thank you so much for your responses. Have a wonderful day. Goodbye!"
        )

    def _rephrase_text(self, q, ctx: FSMContext | None = None) -> str:
        hebrew = ctx and self._is_hebrew(ctx)
        if q is None:
            return "תנסח אחרת בבקשה." if hebrew else "Let me say that differently. Could you please repeat your answer?"
        if hebrew:
            rephrases = {
                "rating": f"אנסח מחדש. {q.prompt} אנא ציין מספר בין 1 ל-10.",
                "mcq": f"אסביר אחרת. {q.prompt} אנא בחר אחת מהאפשרויות.",
                "free_text": f"אנסח אחרת. {q.prompt} ענה בחופשיות — אין תשובה שגויה.",
            }
        else:
            rephrases = {
                "rating": (f"Let me rephrase that. {q.prompt} "
                           "Please give me a number from 1 to 10, where 1 is very dissatisfied "
                           "and 10 is extremely satisfied."),
                "mcq": (f"Let me say that differently. {q.prompt} "
                        "Please choose one of the options I listed."),
                "free_text": (f"Let me put it another way. {q.prompt} "
                              "Please share your thoughts freely — there is no wrong answer."),
            }
        return rephrases.get(q.question_type, q.prompt)

    def _help_text(self, q, ctx: FSMContext | None = None) -> str:
        hebrew = ctx and self._is_hebrew(ctx)
        if q is None:
            return "אני כאן לעזור. ענה על השאלה כשתהיה מוכן." if hebrew else "I'm here to help. Please answer the question when you're ready."
        if hebrew:
            tips = {
                "rating": "פשוט אמור מספר בין 1 ל-10.",
                "mcq": "אמור את האות של האפשרות שאתה מעדיף.",
                "free_text": "אמור בחופשיות מה שעולה בדעתך.",
            }
            return f"אין בעיה! {tips.get(q.question_type, 'אנא ענה על השאלה.')} {q.prompt}"
        tips = {
            "rating": "Just say a number between 1 and 10.",
            "mcq": "Say the letter or number of the option you prefer.",
            "free_text": "Feel free to say whatever comes to mind.",
        }
        return f"No problem! {tips.get(q.question_type, 'Please answer the question.')} {q.prompt}"

    # -----------------------------------------------------------------------
    # Dispatch table
    # -----------------------------------------------------------------------

    _DISPATCH = {
        DialogueState.GREETING:   _handle_waiting,
        DialogueState.ASKING:     _handle_waiting,
        DialogueState.WAITING:    _handle_waiting,
        DialogueState.REPEATING:  _handle_repeating,
        DialogueState.REPHRASING: _handle_rephrasing,
        DialogueState.FALLBACK:   _handle_fallback,
        DialogueState.CONFIRMING: _handle_confirming,
        DialogueState.CLOSING:    _handle_closing_done_error,
        DialogueState.DONE:       _handle_closing_done_error,
        DialogueState.ERROR:      _handle_closing_done_error,
    }
