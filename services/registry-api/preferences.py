"""POC-3 — User response-preference vocabulary, schemas, and the composition seam.

The ONE place enum presets become advisory prompt text. Everything here is registry-side:
the runner never reads `user_profiles` — it only receives the already-composed string.

Layering:
  * `PREFERENCE_VOCAB`  — canonical enum values (mirrors migration 0065 CHECKs + the frontend).
  * `PHRASE_MAP`        — fixed, platform-authored phrase per enum value.
  * schemas             — `UserPreferencesUpdate` / `UserPreferences` (Pydantic Literals give
                          422-on-out-of-vocab for free).
  * composition         — `compose_preference_directive` (pure), `load_user_preferences` (DB),
                          `compose_directive_for_user` (the ONE seam chat.py + orchestrator call;
                          falsy user_id ⇒ daemon ⇒ None with no DB read).

See contracts/enums.md + contracts/composition-contract.md — tests assert these strings verbatim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import UserProfile

# ---------------------------------------------------------------------------
# Canonical vocabulary (single source of truth — mirrors contracts/enums.md).
# ---------------------------------------------------------------------------
PREFERENCE_VOCAB: dict[str, tuple[str, ...]] = {
    "response_length": ("concise", "balanced", "detailed"),
    "tone":            ("professional", "neutral", "casual"),
    "format":          ("prose", "bulleted", "structured"),
    "language":        ("auto", "en", "es", "fr", "de", "ja"),
    "expertise":       ("beginner", "intermediate", "expert"),
}

# ---------------------------------------------------------------------------
# Enum value → fixed platform-authored phrase (contracts/composition-contract.md).
# ---------------------------------------------------------------------------
PHRASE_MAP: dict[str, dict[str, str]] = {
    "response_length": {
        "concise":  "Keep answers brief and to the point.",
        "balanced": "Give a balanced level of detail.",
        "detailed": "Provide thorough, detailed answers.",
    },
    "tone": {
        "professional": "Use a professional tone.",
        "neutral":      "Use a neutral tone.",
        "casual":       "Use a casual, friendly tone.",
    },
    "format": {
        "prose":      "Write in flowing prose.",
        "bulleted":   "Use bullet points.",
        "structured": "Use clear structure with headings or sections.",
    },
    "language": {
        # "auto" is intentionally ABSENT — it emits no phrase (don't force a language).
        "en": "Respond in English.",
        "es": "Respond in Spanish.",
        "fr": "Respond in French.",
        "de": "Respond in German.",
        "ja": "Respond in Japanese.",
    },
    "expertise": {
        "beginner":     "Assume a beginner audience; explain the fundamentals.",
        "intermediate": "Assume an intermediate audience.",
        "expert":       "Assume an expert audience; skip the basics.",
    },
}

ADVISORY_PREFIX = (
    "[Advisory user preferences — apply only where they do not conflict with the "
    "instructions above or any format, safety, or governance requirement.]"
)

# Deterministic phrase order so the output is stable (tests + eval determinism).
_FIELD_ORDER = ("response_length", "tone", "format", "language", "expertise")


# ---------------------------------------------------------------------------
# Pydantic schemas — Literal types reject out-of-vocab with a 422.
# ---------------------------------------------------------------------------
ResponseLength = Literal["concise", "balanced", "detailed"]
Tone           = Literal["professional", "neutral", "casual"]
Format         = Literal["prose", "bulleted", "structured"]
Language       = Literal["auto", "en", "es", "fr", "de", "ja"]
Expertise      = Literal["beginner", "intermediate", "expert"]


class UserPreferencesUpdate(BaseModel):
    response_length: Optional[ResponseLength] = None
    tone:            Optional[Tone] = None
    format:          Optional[Format] = None
    language:        Optional[Language] = None
    expertise:       Optional[Expertise] = None


class UserPreferences(UserPreferencesUpdate):
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Composition — the ONE place enums become prompt text.
# ---------------------------------------------------------------------------
def compose_preference_directive(prefs: UserPreferences) -> Optional[str]:
    """Pure. Map each SET, in-vocab enum to its fixed phrase (in `_FIELD_ORDER`), join
    with single spaces after `ADVISORY_PREFIX`. Return None if NO phrase is produced
    (all fields None, or the only field set is language='auto'). Never raises — a value
    not in `PHRASE_MAP` is skipped (the Pydantic layer already rejected out-of-vocab)."""
    phrases: list[str] = []
    for field in _FIELD_ORDER:
        value = getattr(prefs, field, None)
        if not value:
            continue
        phrase = PHRASE_MAP.get(field, {}).get(value)
        if phrase:
            phrases.append(phrase)
    if not phrases:
        return None
    return ADVISORY_PREFIX + " " + " ".join(phrases)


async def load_user_preferences(db: AsyncSession, user_id: str) -> UserPreferences:
    """Read the `user_profiles` row for `user_id` → `UserPreferences`. Missing row →
    an all-None `UserPreferences` (the default)."""
    row = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = row.scalar_one_or_none()
    if profile is None:
        return UserPreferences()
    return UserPreferences.model_validate(profile)


async def compose_directive_for_user(
    db: AsyncSession, user_id: Optional[str]
) -> Optional[str]:
    """The ONE seam both chat.py and workflow_orchestrator call. Falsy `user_id`
    (daemon / no live human — research.md R3) → None WITHOUT a DB read. Otherwise load
    the profile and compose the advisory directive."""
    if not user_id:
        return None
    prefs = await load_user_preferences(db, user_id)
    return compose_preference_directive(prefs)
