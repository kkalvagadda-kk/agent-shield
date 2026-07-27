import { Server } from "lucide-react";
import { cn } from "../../lib/utils";

/** The chip vocabulary for a tool, in ONE place.
 *
 *  Three surfaces describe the same tool — the picker tile, the Tools table, and
 *  an MCP server's discovered-tools tab — and before this they each spelled the
 *  colours out inline. That is how "high risk" ends up amber on one screen and
 *  red on another: nothing forces them to agree. Importing beats copying. */

const RISK_CLS: Record<string, string> = {
  high: "bg-red-50 text-red-700",
  medium: "bg-amber-50 text-amber-700",
  low: "bg-green-50 text-green-700",
};

const BASE = "text-xs px-1.5 py-0.5 rounded font-medium";

export function RiskChip({ risk }: { risk?: string | null }) {
  if (!risk) return null;
  return <span className={cn(BASE, RISK_CLS[risk] ?? "bg-slate-100 text-slate-600")}>{risk}</span>;
}

/** Where a tool came from.
 *
 *  For an MCP-discovered tool this shows the SOURCE SERVER, not the type. The
 *  type string is `mcp_tool`, and rendering it raw puts a chip reading
 *  "mcp_tool" on the tile — which names the plumbing and hides the one thing
 *  that actually distinguishes two discovered tools from each other. Keyed off
 *  `type === "mcp_tool"` rather than a truthy `mcp_server_name`, so a row whose
 *  denormalized server name is missing still reads as MCP-sourced instead of
 *  silently rendering as native. */
export function ToolSourceChip({
  type,
  mcpServerName,
}: {
  type?: string | null;
  mcpServerName?: string | null;
}) {
  if (type === "mcp_tool") {
    return (
      <span className={cn(BASE, "inline-flex items-center gap-1 bg-indigo-100 text-indigo-700")}>
        <Server size={11} />
        {mcpServerName || "MCP"}
      </span>
    );
  }
  if (!type) return null;
  return <span className={cn(BASE, "bg-slate-100 text-slate-600")}>{type}</span>;
}

/** Only rendered when a tool is NOT active — an `active` chip on every row is
 *  noise, but a deprecated or vanished-upstream tool must never look normal. */
export function ToolStatusChip({ status }: { status?: string | null }) {
  if (!status || status === "active") return null;
  const label = status === "inactive" ? "unavailable" : status;
  return <span className={cn(BASE, "bg-slate-200 text-slate-600")}>{label}</span>;
}

export function PiiChip({ allowed }: { allowed?: boolean | null }) {
  if (!allowed) return null;
  return <span className={cn(BASE, "bg-amber-50 text-amber-700")}>de-anon</span>;
}

/** A tool is offerable in a picker only when it is active. `deprecated` and
 *  `inactive` both mean "do not bind this to anything new": `inactive` is set by
 *  MCP discovery when a tool disappears upstream (mcp_discovery.py — vanished
 *  tools are marked, never row-deleted), so binding one produces an agent that
 *  fails at run time. Exported so the drawer and its tests agree on the rule. */
export function isSelectableTool(status?: string | null): boolean {
  return (status ?? "active") === "active";
}
