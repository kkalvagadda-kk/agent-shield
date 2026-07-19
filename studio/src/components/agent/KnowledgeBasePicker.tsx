import type { KnowledgeBase } from "../../api/knowledgeApi";

interface KnowledgeBasePickerProps {
  kbs: KnowledgeBase[];
  /** Currently-selected KB ids. */
  selected: string[];
  onToggle: (id: string) => void;
}

/** Shared Knowledge Bases multi-select for every agent-editing surface. Attaching
 *  a KB is how an agent gets a scoped `knowledge_search` tool (server-side) — which
 *  is exactly why `knowledge_search` is never a hand-pickable tool. Presentational
 *  only: the caller owns fetching KBs, the selection state, and (on save) the
 *  bind/unbind reconciliation. */
export default function KnowledgeBasePicker({ kbs, selected, onToggle }: KnowledgeBasePickerProps) {
  return (
    <div
      data-testid="kb-picker"
      className="border border-slate-200 rounded-lg max-h-48 overflow-y-auto divide-y divide-slate-100"
    >
      {kbs.length === 0 && (
        <p className="p-3 text-sm text-slate-400 italic">
          No knowledge bases for your team.{" "}
          <a href="/knowledge" className="underline hover:text-slate-600">Create one →</a>
        </p>
      )}
      {kbs.map((kb) => (
        <label
          key={kb.id}
          className="flex items-center gap-3 px-3 py-2 hover:bg-slate-50 cursor-pointer"
        >
          <input
            type="checkbox"
            checked={selected.includes(kb.id)}
            onChange={() => onToggle(kb.id)}
            className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
          />
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium text-slate-800">{kb.name}</span>
            {kb.description && (
              <span className="text-xs text-slate-400 ml-2 truncate">{kb.description}</span>
            )}
          </div>
          <span className="text-xs text-slate-400">{kb.ready_count}/{kb.source_count} ready</span>
        </label>
      ))}
    </div>
  );
}
