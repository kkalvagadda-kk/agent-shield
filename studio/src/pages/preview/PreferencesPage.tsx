import { useState } from "react";
import { SlidersHorizontal, Info } from "lucide-react";
import { PREFERENCE_OPTIONS, DEFAULT_PREFERENCES } from "../../demo/mockData";

const FIELDS: { key: keyof typeof DEFAULT_PREFERENCES; label: string; hint: string }[] = [
  { key: "length", label: "Response length", hint: "How much detail you want" },
  { key: "tone", label: "Tone", hint: "How the agent should sound" },
  { key: "format", label: "Format", hint: "How answers are structured" },
  { key: "expertise", label: "Expertise level", hint: "How much it should explain" },
];

export default function PreferencesPage() {
  const [prefs, setPrefs] = useState({ ...DEFAULT_PREFERENCES });
  const set = (k: keyof typeof prefs, v: string) => setPrefs((p) => ({ ...p, [k]: v }));

  const directive = `User presentation preferences (advisory; task, format, safety, and governance requirements take precedence): length=${prefs.length}, tone=${prefs.tone}, format=${prefs.format}, language=${prefs.language}, expertise=${prefs.expertise}.`;

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <div className="flex items-center gap-2 mb-1">
        <SlidersHorizontal size={20} className="text-blue-600" />
        <h1 className="text-2xl font-bold text-slate-900">Response Preferences</h1>
      </div>
      <p className="text-sm text-slate-500 mb-6">
        Structured presets that apply to <span className="font-medium">any</span> agent you talk to. Preferences are advisory — an agent's task and your org's governance always win.
      </p>

      <div className="card space-y-5">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <div className="flex items-baseline justify-between mb-1.5">
              <label className="label">{f.label}</label>
              <span className="text-xs text-slate-400">{f.hint}</span>
            </div>
            <div className="flex gap-2 flex-wrap">
              {(PREFERENCE_OPTIONS[f.key] as readonly string[]).map((opt) => (
                <button
                  key={opt}
                  onClick={() => set(f.key, opt)}
                  className={`px-3 py-1.5 rounded-md text-sm border capitalize transition-colors ${prefs[f.key] === opt ? "border-blue-500 bg-blue-50 text-blue-700 font-medium" : "border-slate-200 text-slate-600 hover:border-slate-300"}`}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>
        ))}

        <div>
          <label className="label mb-1.5 block">Language</label>
          <select value={prefs.language} onChange={(e) => set("language", e.target.value)} className="input max-w-xs">
            {PREFERENCE_OPTIONS.language.map((l) => <option key={l}>{l}</option>)}
          </select>
        </div>
      </div>

      {/* Compiled directive preview */}
      <div className="mt-5">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">What the agent receives</p>
        <div className="rounded-lg bg-slate-900 text-slate-200 text-xs font-mono p-4 leading-relaxed">{directive}</div>
        <p className="text-xs text-slate-400 mt-2 inline-flex items-start gap-1.5">
          <Info size={12} className="mt-0.5 shrink-0" />
          Compiled by the platform from your presets (never free text), so it's bounded and can't be used for prompt injection.
        </p>
      </div>

      <div className="flex justify-end mt-6">
        <button className="btn-primary">Save preferences</button>
      </div>
    </div>
  );
}
