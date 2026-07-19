import { cn } from "../../lib/utils";
import type { RegistryTool } from "../../api/registryApi";

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

/** Shared Tools checklist for every agent-editing surface. Excludes
 *  `knowledge_search` structurally (callers must also strip it from what they
 *  persist), so the class of bug where one surface forgets the filter cannot
 *  recur. Presentational only — no data fetching, no form coupling. */
export default function ToolsPicker({
  tools,
  selected,
  onToggle,
  emptyText = "No tools available.",
}: ToolsPickerProps) {
  const pickable = tools.filter((t) => t.name !== KNOWLEDGE_SEARCH_TOOL);
  return (
    <div
      data-testid="tools-picker"
      className="border border-slate-200 rounded-lg max-h-48 overflow-y-auto divide-y divide-slate-100"
    >
      {pickable.length === 0 && (
        <p className="p-3 text-sm text-slate-400 italic">{emptyText}</p>
      )}
      {pickable.map((tool) => (
        <label
          key={tool.id}
          className="flex items-center gap-3 px-3 py-2 hover:bg-slate-50 cursor-pointer"
        >
          <input
            type="checkbox"
            checked={selected.includes(tool.name)}
            onChange={() => onToggle(tool.name)}
            className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
          />
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium text-slate-800">{tool.display_name || tool.name}</span>
            {tool.description && (
              <span className="text-xs text-slate-400 ml-2 truncate">{tool.description}</span>
            )}
          </div>
          {tool.risk_level && (
            <span
              className={cn(
                "text-xs px-1.5 py-0.5 rounded font-medium",
                tool.risk_level === "high" && "bg-red-50 text-red-700",
                tool.risk_level === "medium" && "bg-amber-50 text-amber-700",
                tool.risk_level === "low" && "bg-green-50 text-green-700",
              )}
            >
              {tool.risk_level}
            </span>
          )}
        </label>
      ))}
    </div>
  );
}
