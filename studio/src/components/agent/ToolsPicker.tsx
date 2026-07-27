import { Plus, X } from "lucide-react";
import { useMemo, useState } from "react";
import { cn } from "../../lib/utils";
import type { RegistryTool } from "../../api/registryApi";
import PickerTile from "../shared/PickerTile";
import TilePickerDrawer from "../shared/TilePickerDrawer";
import {
  PiiChip,
  RiskChip,
  ToolSourceChip,
  ToolStatusChip,
  isSelectableTool,
} from "../shared/ToolChips";

/** The one tool that is NEVER hand-pickable: it is configured via the Knowledge
 *  Bases picker and attached server-side when a KB is bound. Filtered out here in
 *  ONE place so no agent-editing surface (Create, Settings, Edit modal) can ever
 *  list it as a selectable tool again. */
export const KNOWLEDGE_SEARCH_TOOL = "knowledge_search";

/** Sentinel source-filter values. Real MCP server names occupy the same filter
 *  space, so these two carry a leading space to keep a server literally named
 *  "native" from colliding with the built-in bucket. */
const SOURCE_ALL = " all";
const SOURCE_NATIVE = " native";

interface ToolsPickerProps {
  tools: RegistryTool[];
  /** Currently-selected tool identifiers — names by default, ids when
   *  `valueKey="id"`. */
  selected: string[];
  onToggle: (value: string) => void;
  emptyText?: string;
  /** Which field of a tool the caller stores in `selected`.
   *
   *  Agent bindings persist tool NAMES; skill bundles and the legacy graph canvas
   *  persist tool IDS. Those two callers used to each keep a private copy of this
   *  component purely because of that one difference, and neither copy ever grew
   *  the MCP badge, the active-only filter, or the source filter. An explicit
   *  mode parameter is the fix — not a second component, and not sniffing whether
   *  a string happens to look like a uuid. */
  valueKey?: "name" | "id";
}

function label(tool: RegistryTool) {
  return tool.display_name || tool.name;
}

/** Shared Tools picker for every tool-selecting surface: the selected tools show
 *  inline as removable chips, and "Add from catalog" opens a browse-and-select
 *  tile drawer.
 *
 *  Excludes `knowledge_search` structurally in ONE place (callers must also strip
 *  it from what they persist), so the class of bug where one surface forgets the
 *  filter cannot recur. The filter lives here rather than in the callers on
 *  purpose — scattering it is exactly how it would get dropped.
 *
 *  Presentational only — no data fetching, no form coupling. Callers must pass
 *  the COMPLETE catalog (see `listAllTools`), not a first page: the drawer
 *  filters client-side, so anything the caller failed to fetch is invisible here
 *  with no error, and `/tools/` caps `limit` at 200. */
export default function ToolsPicker({
  tools,
  selected,
  onToggle,
  emptyText = "No tools available.",
  valueKey = "name",
}: ToolsPickerProps) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<string>(SOURCE_ALL);

  const valueOf = (tool: RegistryTool) => (valueKey === "id" ? tool.id : tool.name);

  // `knowledge_search` is excluded by NAME whatever the caller keys selection on —
  // it is one specific platform tool, not a shape.
  const visible = tools.filter((t) => t.name !== KNOWLEDGE_SEARCH_TOOL);

  /** The drawer catalog: active tools only. A deprecated tool, or an MCP tool
   *  whose upstream server stopped advertising it, must not be bindable to
   *  something new. */
  const pickable = visible.filter((t) => isSelectableTool(t.status));

  /** Chips resolve from `visible`, NOT from `pickable`. If an agent already binds
   *  a tool that has since been deprecated, dropping it from the chip row would
   *  leave the binding in place while making it invisible — the user could
   *  neither see it nor remove it. It stays visible, marked unavailable, and
   *  removable. */
  const selectedTools = visible.filter((t) => selected.includes(valueOf(t)));

  /** Source buckets, derived from the catalog — no extra endpoint. Only shown
   *  once at least one MCP server is represented; on a native-only install the
   *  control would be a single dead button. */
  const sources = useMemo(() => {
    const servers = new Map<string, number>();
    let native = 0;
    for (const t of pickable) {
      if (t.type === "mcp_tool") {
        const key = t.mcp_server_name || "MCP";
        servers.set(key, (servers.get(key) ?? 0) + 1);
      } else {
        native += 1;
      }
    }
    if (servers.size === 0) return [];
    return [
      { key: SOURCE_ALL, label: "All", count: pickable.length },
      ...(native > 0 ? [{ key: SOURCE_NATIVE, label: "Native", count: native }] : []),
      ...[...servers.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([name, count]) => ({ key: name, label: name, count })),
    ];
  }, [pickable]);

  const matchesSource = (t: RegistryTool) => {
    if (source === SOURCE_ALL) return true;
    if (source === SOURCE_NATIVE) return t.type !== "mcp_tool";
    return t.type === "mcp_tool" && (t.mcp_server_name || "MCP") === source;
  };

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
            className={cn(
              "inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded border text-xs",
              isSelectableTool(tool.status)
                ? "border-slate-200 bg-slate-50 text-slate-700"
                : "border-amber-300 bg-amber-50 text-amber-800",
            )}
            title={
              isSelectableTool(tool.status)
                ? undefined
                : `This tool is ${tool.status} and can no longer be bound to a new agent.`
            }
          >
            {label(tool)}
            {tool.type === "mcp_tool" && tool.mcp_server_name && (
              <span className="text-slate-400">· {tool.mcp_server_name}</span>
            )}
            {!isSelectableTool(tool.status) && <span className="font-medium">(unavailable)</span>}
            <button
              type="button"
              onClick={() => onToggle(valueOf(tool))}
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
        searchPlaceholder="Search by name, description, or source server"
        toolbar={
          sources.length > 0 ? (
            <div className="flex flex-wrap gap-1.5" data-testid="tools-source-filter">
              {sources.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setSource(s.key)}
                  aria-pressed={source === s.key}
                  className={cn(
                    "text-xs px-2 py-1 rounded-full border transition-colors",
                    source === s.key
                      ? "border-indigo-400 bg-indigo-50 text-indigo-700 font-medium"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  )}
                >
                  {s.label}
                  <span className="ml-1 text-slate-400">{s.count}</span>
                </button>
              ))}
            </div>
          ) : null
        }
      >
        {(search) => {
          const q = search.trim().toLowerCase();
          const shown = pickable.filter((t) => {
            if (!matchesSource(t)) return false;
            if (!q) return true;
            return (
              label(t).toLowerCase().includes(q) ||
              t.name.toLowerCase().includes(q) ||
              (t.description ?? "").toLowerCase().includes(q) ||
              (t.mcp_server_name ?? "").toLowerCase().includes(q)
            );
          });
          if (shown.length === 0) {
            return (
              <p className="text-sm text-slate-400 italic col-span-full">
                {q
                  ? `No tools match “${search}”. Tools are managed under Tools.`
                  : "No tools from this source."}
              </p>
            );
          }
          return shown.map((tool) => (
            <PickerTile
              key={tool.id}
              title={label(tool)}
              description={tool.description}
              selected={selected.includes(valueOf(tool))}
              onToggle={() => onToggle(valueOf(tool))}
              meta={
                <>
                  <RiskChip risk={tool.risk_level} />
                  <ToolSourceChip type={tool.type} mcpServerName={tool.mcp_server_name} />
                  <PiiChip allowed={tool.pii_deanonymize_allowed} />
                  <ToolStatusChip status={tool.status} />
                </>
              }
            />
          ));
        }}
      </TilePickerDrawer>
    </div>
  );
}
