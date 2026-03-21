"""SAA-58: Reference test utterances for each intent.

These are used by unit tests to validate the classifier coverage.
"""

from .schema import IntentType

TEST_UTTERANCES: dict[IntentType, list[str]] = {
    IntentType.REPEAT: [
        "Can you repeat that?",
        "Say again please",
        "I didn't hear you",
        "Pardon?",
        "What did you say?",
        "Come again",
        "Once more please",
        "I didn't catch that",
    ],
    IntentType.REPHRASE: [
        "Can you rephrase that?",
        "I don't understand",
        "Can you explain it a different way?",
        "What do you mean?",
        "Clarify please",
        "I'm not sure what you mean",
    ],
    IntentType.NOT_NOW: [
        "Not now",
        "Call me back later",
        "I'm busy right now",
        "This is a bad time",
        "Maybe later",
        "Call back another time",
        "Not a good time",
    ],
    IntentType.SKIP: [
        "Skip",
        "Next question",
        "Pass",
        "I'd like to skip that",
        "Move on please",
        "Don't want to answer",
    ],
    IntentType.HELP: [
        "Help",
        "I'm confused",
        "I don't know how to answer",
        "I need assistance",
        "I'm lost",
    ],
    IntentType.CONFIRM_YES: [
        "Yes",
        "Yeah that's right",
        "Correct",
        "Absolutely",
        "Sure",
        "Confirm",
        "That's right",
    ],
    IntentType.CONFIRM_NO: [
        "No",
        "That's wrong",
        "Incorrect",
        "Nope",
        "Not right",
        "Negative",
    ],
    IntentType.ANSWER: [
        # Rating answers (question_type="rating")
        "I'd say seven",          # free-text, won't parse without type
        "Three",
        "My answer is five",
        # MCQ (question_type="mcq")
        "Option A",
        "The second one",
        "B",
        # Free text
        "I think the service was excellent and very responsive",
    ],
}
