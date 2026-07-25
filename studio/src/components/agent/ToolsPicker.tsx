import { Plus, X } from "lucide-react";
import { useState } from "react";
import { cn } from "../../lib/utils";
import type { RegistryTool } from "../../api/registryApi";
import PickerTile from "../shared/PickerTile";
import TilePickerDrawer from "../shared/TilePickerDrawer";

/** The one tool that is NEVER hand-pickable: it is configured via the Knowledge
 *  Bases picker and attached server-side when a KB is bound. Filtered out here in
 *  ONE place so no agent-editing surface (Create, Settings, Edit modal) can ever
 *  list it as a selectable tool again. */
export const KNOWLEDGE_SEARCH_TOOL = "knowledge_search";

interface ToolsPickerProps {
  tools: RegistryTool[];
  /** Currently-selected tool names. */
  selected: string[];
  onToggle: (name: string) => void;
  emptyText?: string;
}

const RISK_CHIP: Record<string, string> = {
  high: "bg-red-50 text-red-700",
  medium: "bg-amber-50 text-amber-700",
  low: "bg-green-50 text-green-700",
};

function label(tool: RegistryTool) {
  return tool.display_name || tool.name;
}

/** Shared Tools picker for every agent-editing surface: the selected tools show
 *  inline as removable chips, and "Add from catalog" opens a browse-and-select
 *  tile drawer.
 *
 *  Excludes `knowledge_search` structurally in ONE place (callers must also strip
 *  it from what they persist), so the class of bug where one surface forgets the
 *  filter cannot recur. The filter lives here rather than in the three callers on
 *  purpose — scattering it is exactly how it would get dropped.
 *
 *  Presentational only — no data fetching, no form coupling. Prop signature is
 *  unchanged from the previous checkbox-list version so no caller needed edits. */
export default function ToolsPicker({
  tools,
  selected,
  onToggle,
  emptyText = "No tools available.",
}: ToolsPickerProps) {
  const [open, setOpen] = useState(false);
  const pickable = tools.filter((t) => t.name !== KNOWLEDGE_SEARCH_TOOL);
  const selectedTools = pickable.filter((t) => selected.includes(t.name));

  return (
    <div data-testid="tools-picker">
      {/* Selected tools — compact removable chips, so the builder surface stays
          short no matter how many tools exist in the catalog. */}
      <div className="flex flex-wrap items-center gap-1.5">
        {selectedTools.length === 0 && (
          <span className="text-sm text-slate-400 italic">No tools selected.</span>
        )}
        {selectedTools.map((tool) => (
          <span
            key={tool.id}
            className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded border border-slate-200 bg-slate-50 text-xs text-slate-700"
          >
            {label(tool)}
            <button
              type="button"
              onClick={() => onToggle(tool.name)}
              className="text-slate-400 hover:text-slate-700"
              aria-label={`Remove ${label(tool)}`}
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
        title="Add tools"
        selectedCount={selectedTools.length}
        isEmpty={pickable.length === 0}
        emptyText={emptyText}
        testId="tools-picker-drawer"
      >
        {(search) => {
          const q = search.trim().toLowerCase();
          const shown = q
            ? pickable.filter(
                (t) =>
                  label(t).toLowerCase().includes(q) ||
                  t.name.toLowerCase().includes(q) ||
                  (t.description ?? "").toLowerCase().includes(q),
              )
            : pickable;
          if (shown.length === 0) {
            return (
              <p className="text-sm text-slate-400 italic col-span-full">
                No tools match “{search}”. Tools are managed under Tools.
              </p>
            );
          }
          return shown.map((tool) => (
            <PickerTile
              key={tool.id}
              title={label(tool)}
              description={tool.description}
              selected={selected.includes(tool.name)}
              onToggle={() => onToggle(tool.name)}
              meta={
                <>
                  {tool.risk_level && (
                    <span
                      className={cn(
                        "text-xs px-1.5 py-0.5 rounded font-medium",
                        RISK_CHIP[tool.risk_level],
                      )}
                    >
                      {tool.risk_level}
                    </span>
                  )}
                  {tool.type && (
                    <span className="text-xs px-1.5 py-0.5 rounded font-medium bg-slate-100 text-slate-600">
                      {tool.type}
                    </span>
                  )}
                </>
              }
            />
          ));
        }}
      </TilePickerDrawer>
    </div>
  );
}
