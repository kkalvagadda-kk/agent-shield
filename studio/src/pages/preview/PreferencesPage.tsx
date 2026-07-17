import { useEffect, useState } from "react";
import { SlidersHorizontal, Info } from "lucide-react";
import { toast } from "sonner";
import {
  getMyPreferences,
  updateMyPreferences,
  type UserPreferences,
} from "../../api/registryApi";

// Enum vocabularies — MUST match the backend (preferences.py _ENUMS). Enum-only,
// so a preference can never be a free-text prompt-injection vector.
const OPTIONS: Record<keyof UserPreferences, readonly string[]> = {
  response_length: ["concise", "balanced", "detailed"],
  tone: ["professional", "neutral", "casual"],
  format: ["prose", "bulleted", "structured"],
  language: ["auto", "en", "es", "fr", "de", "ja"],
  expertise: ["beginner", "intermediate", "expert"],
};

const BUTTON_FIELDS: { key: keyof UserPreferences; label: string; hint: string }[] = [
  { key: "response_length", label: "Response length", hint: "How much detail you want" },
  { key: "tone", label: "Tone", hint: "How the agent should sound" },
  { key: "format", label: "Format", hint: "How answers are structured" },
  { key: "expertise", label: "Expertise level", hint: "How much it should explain" },
];

const EMPTY: UserPreferences = {
  response_length: null,
  tone: null,
  format: null,
  language: null,
  expertise: null,
};

export default function PreferencesPage() {
  const [prefs, setPrefs] = useState<UserPreferences>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    getMyPreferences()
      .then((p) => {
        if (alive) setPrefs({ ...EMPTY, ...p });
      })
      .catch(() => {
        // No row yet (or transient) → all-null defaults; not an error state.
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Toggle: clicking the active value clears it (null = no preference for that dimension).
  const set = (k: keyof UserPreferences, v: string) =>
    setPrefs((p) => ({ ...p, [k]: p[k] === v ? null : v }));

  const save = async () => {
    setSaving(true);
    try {
      const saved = await updateMyPreferences(prefs);
      setPrefs({ ...EMPTY, ...saved });
      toast.success("Preferences saved");
    } catch {
      toast.error("Couldn't save preferences. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <div className="flex items-center gap-2 mb-1">
        <SlidersHorizontal size={20} className="text-blue-600" />
        <h1 className="text-2xl font-bold text-slate-900">Response Preferences</h1>
      </div>
      <p className="text-sm text-slate-500 mb-6">
        Structured presets that apply to <span className="font-medium">any</span> agent you
        talk to. Preferences are advisory — an agent's task and your org's governance always win.
      </p>

      <div className="card space-y-5" aria-busy={loading}>
        {BUTTON_FIELDS.map((f) => (
          <div key={f.key}>
            <div className="flex items-baseline justify-between mb-1.5">
              <label className="label">{f.label}</label>
              <span className="text-xs text-slate-400">{f.hint}</span>
            </div>
            <div className="flex gap-2 flex-wrap">
              {OPTIONS[f.key].map((opt) => (
                <button
                  key={opt}
                  type="button"
                  aria-pressed={prefs[f.key] === opt}
                  onClick={() => set(f.key, opt)}
                  className={`px-3 py-1.5 rounded-md text-sm border capitalize transition-colors ${
                    prefs[f.key] === opt
                      ? "border-blue-500 bg-blue-50 text-blue-700 font-medium"
                      : "border-slate-200 text-slate-600 hover:border-slate-300"
                  }`}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>
        ))}

        <div>
          <label className="label mb-1.5 block" htmlFor="pref-language">
            Language
          </label>
          <select
            id="pref-language"
            value={prefs.language ?? "auto"}
            onChange={(e) => set("language", e.target.value)}
            className="input max-w-xs"
          >
            {OPTIONS.language.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
      </div>

      <p className="text-xs text-slate-400 mt-4 inline-flex items-start gap-1.5">
        <Info size={12} className="mt-0.5 shrink-0" />
        Compiled by the platform from your presets (never free text), so it's bounded and
        can't be used for prompt injection. A preference never overrides a task, format,
        safety, or governance requirement.
      </p>

      <div className="flex justify-end mt-6">
        <button
          type="button"
          className="btn-primary"
          onClick={save}
          disabled={loading || saving}
        >
          {saving ? "Saving…" : "Save preferences"}
        </button>
      </div>
    </div>
  );
}
