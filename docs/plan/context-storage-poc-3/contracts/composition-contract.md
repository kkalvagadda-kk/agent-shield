# POC-3 — Advisory-Directive Composition Contract

The ONE place enums become prompt text. Implemented in
`services/registry-api/preferences.py`. Read `contracts/enums.md` first — this file maps each
canonical enum value to its fixed, platform-authored phrase.

## Precedence framing (design §2.2 — non-negotiable)

`governance > author instructions > workflow settings > user preference (lowest)`.

Enforced two ways:
1. **Position** — the directive is injected as a `SystemMessage` that lands AFTER the author
   instructions in the runner (research.md R6). Last = weakest.
2. **Wording** — the advisory prefix explicitly says a preference yields to anything above it or
   to any format/safety/governance requirement.

## `PHRASE_MAP` (exact strings — tests assert these verbatim)

```python
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
```

## Function contracts

```python
def compose_preference_directive(prefs: "UserPreferences") -> str | None:
    """Pure function. Map each SET, in-vocab enum to its fixed phrase (in _FIELD_ORDER),
    join with single spaces after ADVISORY_PREFIX. Return None if NO phrase is produced
    (all fields None, or the only field set is language='auto'). Never raises; a value
    not in PHRASE_MAP is skipped (the Pydantic layer already rejected out-of-vocab)."""

async def load_user_preferences(db: AsyncSession, user_id: str) -> "UserPreferences":
    """Read the user_profiles row for user_id → UserPreferences. Missing row → an
    all-None UserPreferences (the default)."""

async def compose_directive_for_user(db: AsyncSession, user_id: str | None) -> str | None:
    """The ONE seam both chat.py and workflow_orchestrator call. Falsy user_id (daemon /
    no live human, research.md R3) → None WITHOUT a DB read. Else load + compose."""
```

## Worked examples (assert verbatim in tests)

Input `{response_length: "concise", format: "bulleted", expertise: "expert"}`:

```
[Advisory user preferences — apply only where they do not conflict with the instructions above or any format, safety, or governance requirement.] Keep answers brief and to the point. Use bullet points. Assume an expert audience; skip the basics.
```

Input `{tone: "casual", language: "es"}`:

```
[Advisory user preferences — apply only where they do not conflict with the instructions above or any format, safety, or governance requirement.] Use a casual, friendly tone. Respond in Spanish.
```

Edge cases:

| Input | Output |
|---|---|
| all fields `None` | `None` |
| `{language: "auto"}` only | `None` (auto emits no phrase) |
| `{language: "auto", tone: "neutral"}` | prefix + `Use a neutral tone.` (auto still omitted) |
| `compose_directive_for_user(db, "")` | `None` (daemon; no DB read) |
| `compose_directive_for_user(db, None)` | `None` (daemon; no DB read) |
| `compose_directive_for_user(db, "<user with no row>")` | `None` (all-None prefs) |

## Two-user divergence (the headline proof)

`compose_directive_for_user(db, USER_A)` where A = `{format: bulleted, response_length: concise}`
and `compose_directive_for_user(db, USER_B)` where B = `{format: prose, response_length: detailed}`
MUST return two **different** non-None strings. suite-76 asserts inequality at the API/DB layer.
