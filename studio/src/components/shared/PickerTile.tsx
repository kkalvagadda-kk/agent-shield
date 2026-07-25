import { Check } from "lucide-react";
import { cn } from "../../lib/utils";

interface PickerTileProps {
  title: string;
  description?: string | null;
  /** Badges/chips describing the entity — risk + type for a tool, source counts
   *  for a knowledge base. Deliberately a slot rather than a fixed `risk` prop:
   *  knowledge bases have no risk level, and rendering a "low" risk badge on one
   *  would be meaningless. */
  meta?: React.ReactNode;
  selected: boolean;
  onToggle: () => void;
}

/** One selectable tile in a browse-and-select picker. Selection ONLY — a tile
 *  never edits or deletes the entity it shows. Tools and knowledge bases are
 *  shared, team-scoped resources, so a destructive action sitting on the same
 *  tile as "attach this to my agent" would let a mis-click during agent assembly
 *  destroy something other agents depend on. Editing/deleting lives on the
 *  management pages, where that IS the point of the screen.
 *
 *  Wraps a real <input type="checkbox"> rather than a div with role="checkbox" so
 *  assistive tech and tests both get genuine checkbox semantics (`.checked`). */
export default function PickerTile({
  title,
  description,
  meta,
  selected,
  onToggle,
}: PickerTileProps) {
  return (
    <label
      className={cn(
        "relative flex flex-col gap-1.5 p-3 rounded-lg border cursor-pointer transition-colors text-left",
        selected
          ? "border-indigo-400 bg-indigo-50/40 ring-1 ring-indigo-200"
          : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50",
      )}
    >
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="mt-0.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 shrink-0"
        />
        <span className="text-sm font-medium text-slate-800 leading-snug flex-1 min-w-0 break-words">
          {title}
        </span>
        {selected && <Check size={14} className="text-indigo-600 shrink-0 mt-0.5" />}
      </div>

      {description && (
        // Clamped to 2 lines: descriptions are now multi-line, and an unbounded
        // one would make tiles ragged.
        <p className="text-xs text-slate-500 leading-relaxed line-clamp-2 pl-6">
          {description}
        </p>
      )}

      {meta && <div className="flex items-center gap-1.5 flex-wrap pl-6">{meta}</div>}
    </label>
  );
}
