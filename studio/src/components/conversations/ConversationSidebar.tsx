import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import {
  listConversations,
  listMyConversations,
  listWorkflowConversations,
  type ConversationSummary,
} from "../../api/registryApi";
import { cn } from "../../lib/utils";

// ---------------------------------------------------------------------------
// ConversationSidebar (POC-5) — one shared, presentational-ish list + filter
// mounted at three surfaces (docked History in AgentChatPage/CatalogChatPage,
// the standalone /conversations page, and the deployment Conversations tab).
// It fetches the summary list via React Query and renders rows; it does NOT
// fetch transcripts — each consumer seeds/navigates on `onSelect`. Environment
// is already derived server-side, so the All/Sandbox/Production filter is a
// pure client predicate (`filterConversationsByEnv`).
// ---------------------------------------------------------------------------

export type EnvFilter = "all" | "sandbox" | "production";

/**
 * Pure env predicate: "all" passes everything through; otherwise only rows whose
 * `environment` matches. Exported so the standalone page and tests can reuse it.
 */
export function filterConversationsByEnv(
  items: ConversationSummary[],
  env: EnvFilter,
): ConversationSummary[] {
  return env === "all" ? items : items.filter((c) => c.environment === env);
}

/** Discriminated scope — an explicit union, never an implicit env sniff. */
export type ConversationScope =
  | { kind: "agent"; agentName: string; deploymentId?: string }
  | { kind: "workflow"; workflowId: string; deploymentId?: string }
  | { kind: "me" };

export interface ConversationSidebarProps {
  scope: ConversationScope;
  activeThreadId: string | null;
  onSelect: (summary: ConversationSummary) => void;
  onNew: () => void;
  showEnvFilter?: boolean; // standalone page only
  disabled?: boolean; // block select/new while streaming / awaiting approval
  className?: string;
}

// Small inline relative-time formatter — the repo has no shared helper (checked
// studio/src/lib), so keep it local and dependency-free.
function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const min = Math.round(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 7) return `${day}d ago`;
  const wk = Math.round(day / 7);
  if (wk < 5) return `${wk}w ago`;
  return new Date(iso).toLocaleDateString();
}

const ENV_FILTERS: EnvFilter[] = ["all", "sandbox", "production"];

export default function ConversationSidebar({
  scope,
  activeThreadId,
  onSelect,
  onNew,
  showEnvFilter = false,
  disabled = false,
  className,
}: ConversationSidebarProps) {
  const [envFilter, setEnvFilter] = useState<EnvFilter>("all");

  const { data: conversations = [], isLoading } = useQuery({
    queryKey: ["conversations", scope],
    queryFn: () => {
      switch (scope.kind) {
        case "agent":
          return listConversations(scope.agentName, { deployment_id: scope.deploymentId });
        case "workflow":
          return listWorkflowConversations(scope.workflowId);
        case "me":
          return listMyConversations();
      }
    },
  });

  const visible = showEnvFilter
    ? filterConversationsByEnv(conversations, envFilter)
    : conversations;

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <button
        type="button"
        onClick={onNew}
        disabled={disabled}
        className="btn-secondary w-full justify-center"
      >
        <Plus size={14} /> New conversation
      </button>

      {showEnvFilter && (
        <div className="flex gap-1">
          {ENV_FILTERS.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => setEnvFilter(e)}
              className={cn(
                "px-2.5 py-1 rounded-md text-xs capitalize transition-colors",
                envFilter === e
                  ? "bg-slate-800 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200",
              )}
            >
              {e}
            </button>
          ))}
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-slate-400 px-1 py-2">Loading conversations…</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-slate-400 px-1 py-2">No conversations yet.</p>
      ) : (
        <div className="space-y-1.5">
          {visible.map((c) => {
            const active = c.thread_id === activeThreadId;
            return (
              <button
                key={c.thread_id}
                type="button"
                onClick={() => onSelect(c)}
                disabled={disabled}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "w-full text-left rounded-lg border px-3 py-2.5 transition-all",
                  "disabled:cursor-not-allowed disabled:opacity-60",
                  active
                    ? "border-blue-400 bg-blue-50 ring-1 ring-blue-100"
                    : "border-slate-200 bg-white hover:border-slate-300",
                )}
              >
                <div className="flex items-center justify-between gap-2 mb-0.5">
                  <p className="text-sm font-medium text-slate-900 truncate">
                    {c.title ?? "Untitled conversation"}
                  </p>
                  <span
                    className={cn(
                      "badge text-[10px] shrink-0",
                      c.environment === "production"
                        ? "bg-green-100 text-green-700"
                        : "bg-amber-100 text-amber-700",
                    )}
                  >
                    {c.environment === "production" ? "prod" : "sbx"}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 truncate">
                  {c.agent_name} · {c.message_count} turns · {formatRelativeTime(c.last_activity)}
                </p>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
