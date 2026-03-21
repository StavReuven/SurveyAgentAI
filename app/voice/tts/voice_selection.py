"""SAA-66: Voice selection — choose the best voice for a given language/locale."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VoiceProfile:
    id: str
    name: str
    language: str        # BCP-47 language tag e.g. "en-US"
    gender: str          # "female" | "male" | "neutral"
    style: str           # "conversational" | "news" | "friendly"
    provider: str        # "mock" | "google" | "aws" | "azure"


# ---------------------------------------------------------------------------
# Built-in voice registry
# ---------------------------------------------------------------------------

VOICE_REGISTRY: list[VoiceProfile] = [
    # English
    VoiceProfile("en-US-f-conv", "Emma",    "en-US", "female", "conversational", "mock"),
    VoiceProfile("en-US-m-conv", "James",   "en-US", "male",   "conversational", "mock"),
    VoiceProfile("en-GB-f-conv", "Sophie",  "en-GB", "female", "friendly",       "mock"),
    VoiceProfile("en-AU-f-conv", "Olivia",  "en-AU", "female", "conversational", "mock"),
    # Spanish
    VoiceProfile("es-ES-f-conv", "Lucia",   "es-ES", "female", "conversational", "mock"),
    VoiceProfile("es-MX-f-conv", "Valentina","es-MX","female", "friendly",       "mock"),
    # French
    VoiceProfile("fr-FR-f-conv", "Marie",   "fr-FR", "female", "conversational", "mock"),
    # German
    VoiceProfile("de-DE-f-conv", "Anna",    "de-DE", "female", "conversational", "mock"),
    # Hebrew
    VoiceProfile("he-IL-f-conv", "Tamar",   "he-IL", "female", "conversational", "mock"),
    VoiceProfile("he-IL-m-conv", "Noam",    "he-IL", "male",   "conversational", "mock"),
]

# Map language → fallback voice id
_FALLBACK_VOICE_ID: dict[str, str] = {
    "en": "en-US-f-conv",
    "es": "es-ES-f-conv",
    "fr": "fr-FR-f-conv",
    "de": "de-DE-f-conv",
    "he": "he-IL-f-conv",
}
_DEFAULT_FALLBACK_ID = "en-US-f-conv"


class VoiceSelector:
    """Select the most appropriate voice for a given language/locale."""

    def __init__(self, registry: list[VoiceProfile] | None = None) -> None:
        self._registry = registry or VOICE_REGISTRY

    def select(
        self,
        language: str,
        locale: str | None = None,
        gender_preference: str | None = None,
    ) -> VoiceProfile:
        """Return best matching VoiceProfile.

        Matching priority:
        1. Exact locale match (e.g. "en-US") + gender preference
        2. Exact locale match, any gender
        3. Language prefix match (e.g. "en") + gender preference
        4. Language prefix match, any gender
        5. Registered fallback for language
        6. Absolute default (en-US female)
        """
        lang_tag = (locale or language or "en").lower()
        lang_prefix = lang_tag.split("-")[0]

        candidates = self._registry

        # Pass 1: exact locale
        exact = [v for v in candidates if v.language.lower() == lang_tag]
        result = self._pick_gender(exact, gender_preference)
        if result:
            return result

        # Pass 2: language prefix
        prefix_match = [v for v in candidates if v.language.lower().startswith(lang_prefix)]
        result = self._pick_gender(prefix_match, gender_preference)
        if result:
            return result

        # Pass 3: registered fallback
        fallback_id = _FALLBACK_VOICE_ID.get(lang_prefix, _DEFAULT_FALLBACK_ID)
        fallback = next((v for v in candidates if v.id == fallback_id), None)
        if fallback:
            return fallback

        # Pass 4: absolute default
        default = next((v for v in candidates if v.id == _DEFAULT_FALLBACK_ID), candidates[0])
        return default

    def get_by_id(self, voice_id: str) -> VoiceProfile | None:
        return next((v for v in self._registry if v.id == voice_id), None)

    def _pick_gender(
        self, voices: list[VoiceProfile], gender: str | None
    ) -> VoiceProfile | None:
        if not voices:
            return None
        if gender:
            gendered = [v for v in voices if v.gender == gender]
            if gendered:
                return gendered[0]
        return voices[0]
