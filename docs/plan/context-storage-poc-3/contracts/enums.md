# POC-3 — Canonical Preference Enums (single source of truth)

Every layer — migration `CHECK`, Pydantic validator, `PHRASE_MAP`, frontend option lists,
tests — MUST use exactly these values. If you change one, change all. See `research.md` R1 for
why these were chosen over the two competing drafts.

## Fields, columns, and vocabularies

| API / column field | DB column | Allowed values (NULL always allowed = "no preference") |
|---|---|---|
| `response_length` | `response_length text` | `concise`, `balanced`, `detailed` |
| `tone` | `tone text` | `professional`, `neutral`, `casual` |
| `format` | `format text` | `prose`, `bulleted`, `structured` |
| `language` | `language text` | `auto`, `en`, `es`, `fr`, `de`, `ja` |
| `expertise` | `expertise text` | `beginner`, `intermediate`, `expert` |

- `NULL` on any column = that dimension is omitted from the directive.
- `language = "auto"` = **treated like NULL for the directive** (do not force a language; keeps
  eval/daemon output stable). It is a storable, selectable value but emits **no** phrase.
- Any value outside a column's set → the `PUT` endpoint returns **422** (Pydantic enum reject).

## Canonical Python constant (`services/registry-api/preferences.py`)

```python
PREFERENCE_VOCAB: dict[str, tuple[str, ...]] = {
    "response_length": ("concise", "balanced", "detailed"),
    "tone":            ("professional", "neutral", "casual"),
    "format":          ("prose", "bulleted", "structured"),
    "language":        ("auto", "en", "es", "fr", "de", "ja"),
    "expertise":       ("beginner", "intermediate", "expert"),
}
```

## Canonical frontend constant (`studio/src/pages/PreferencesPage.tsx`)

```ts
// Codes must match contracts/enums.md exactly; labels are display-only.
export const PREFERENCE_OPTIONS = {
  response_length: [
    { value: "concise",  label: "Concise" },
    { value: "balanced", label: "Balanced" },
    { value: "detailed", label: "Detailed" },
  ],
  tone: [
    { value: "professional", label: "Professional" },
    { value: "neutral",      label: "Neutral" },
    { value: "casual",       label: "Casual" },
  ],
  format: [
    { value: "prose",      label: "Prose" },
    { value: "bulleted",   label: "Bulleted" },
    { value: "structured", label: "Structured" },
  ],
  language: [
    { value: "auto", label: "Auto (don't force)" },
    { value: "en",   label: "English" },
    { value: "es",   label: "Spanish" },
    { value: "fr",   label: "French" },
    { value: "de",   label: "German" },
    { value: "ja",   label: "Japanese" },
  ],
  expertise: [
    { value: "beginner",     label: "Beginner" },
    { value: "intermediate", label: "Intermediate" },
    { value: "expert",       label: "Expert" },
  ],
} as const;
```

## Migration CHECK constraints (guarded, idempotent — see `data-model.md`)

```
ck_user_profiles_response_length  CHECK (response_length IS NULL OR response_length IN ('concise','balanced','detailed'))
ck_user_profiles_tone             CHECK (tone            IS NULL OR tone            IN ('professional','neutral','casual'))
ck_user_profiles_format           CHECK (format          IS NULL OR format          IN ('prose','bulleted','structured'))
ck_user_profiles_language         CHECK (language        IS NULL OR language        IN ('auto','en','es','fr','de','ja'))
ck_user_profiles_expertise        CHECK (expertise       IS NULL OR expertise       IN ('beginner','intermediate','expert'))
```
