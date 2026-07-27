import { Plus, X } from "lucide-react";
import { useState } from "react";
import type { KnowledgeBase } from "../../api/knowledgeApi";
import PickerTile from "../shared/PickerTile";
import TilePickerDrawer from "../shared/TilePickerDrawer";

interface KnowledgeBasePickerProps {
  kbs: KnowledgeBase[];
  /** Currently-selected KB ids. */
  selected: string[];
  onToggle: (id: string) => void;
}

/** Shared Knowledge Bases picker for every agent-editing surface: selected KBs
 *  show inline as removable chips, and "Add from catalog" opens the same
 *  browse-and-select tile drawer the Tools picker uses.
 *
 *  Attaching a KB is how an agent gets a scoped `knowledge_search` tool
 *  (server-side) — which is exactly why `knowledge_search` is never a
 *  hand-pickable tool. Presentational only: the caller owns fetching KBs, the
 *  selection state, and (on save) the bind/unbind reconciliation.
 *
 *  Prop signature is unchanged from the previous checkbox-list version, so no
 *  caller needed edits. */
export default function KnowledgeBasePicker({
  kbs,
  selected,
  onToggle,
}: KnowledgeBasePickerProps) {
  const [open, setOpen] = useState(false);
  const selectedKbs = kbs.filter((kb) => selected.includes(kb.id));

  return (
    <div data-testid="kb-picker">
      <div className="flex flex-wrap items-center gap-1.5">
        {selectedKbs.length === 0 && (
          <span className="text-sm text-slate-400 italic">
            No knowledge bases selected.
          </span>
        )}
        {selectedKbs.map((kb) => (
          <span
            key={kb.id}
            className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded border border-slate-200 bg-slate-50 text-xs text-slate-700"
          >
            {kb.name}
            <button
              type="button"
              onClick={() => onToggle(kb.id)}
              className="text-slate-400 hover:text-slate-700"
              aria-label={`Remove ${kb.name}`}
            >
              <X size={12} />
            </button>
          </span>
        ))}
      </div>

      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn-secondary mt-2"
      >
        <Plus size={13} />
        Add from catalog
      </button>

      <TilePickerDrawer
        open={open}
        onClose={() => setOpen(false)}
        title="Add knowledge bases"
        selectedCount={selectedKbs.length}
        isEmpty={kbs.length === 0}
        emptyText={
          <>
            No knowledge bases for your team. Knowledge bases are managed under
            Knowledge.
          </>
        }
        testId="kb-picker-drawer"
      >
        {(search) => {
          const q = search.trim().toLowerCase();
          const shown = q
            ? kbs.filter(
                (kb) =>
                  kb.name.toLowerCase().includes(q) ||
                  (kb.description ?? "").toLowerCase().includes(q),
              )
            : kbs;
          if (shown.length === 0) {
            return (
              <p className="text-sm text-slate-400 italic col-span-full">
                No knowledge bases match “{search}”.
              </p>
            );
          }
          return shown.map((kb) => (
            <PickerTile
              key={kb.id}
              title={kb.name}
              description={kb.description}
              selected={selected.includes(kb.id)}
              onToggle={() => onToggle(kb.id)}
              // KBs have no risk level — the meaningful signal is how much of the
              // corpus is actually queryable.
              meta={
                <span className="text-xs px-1.5 py-0.5 rounded font-medium bg-slate-100 text-slate-600">
                  {kb.ready_count}/{kb.source_count} ready
                </span>
              }
            />
          ));
        }}
      </TilePickerDrawer>
    </div>
  );
}
