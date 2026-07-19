import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Bot, Database, MessagesSquare, User } from "lucide-react";
import ConversationSidebar from "../components/conversations/ConversationSidebar";
import {
  listMemory,
  type ConversationSummary,
  type MemoryMessage,
} from "../api/registryApi";
import { cn } from "../lib/utils";

// ---------------------------------------------------------------------------
// ConversationsPage (POC-5) — the REAL standalone /conversations surface.
// Two panes: left = the shared cross-agent ConversationSidebar (scope {kind:"me"})
// with the All/Sandbox/Production env filter enabled; right = a READ-ONLY
// transcript preview of the selected thread (via listMemory) plus a Continue
// button that hands off to AgentChatPage's ?session seed. This is a sibling to
// the demo mock at pages/preview/ConversationsPage.tsx — no ConsoleContextBar,
// no amber "preview" banner (those belong to the mock).
// ---------------------------------------------------------------------------

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function ConversationsPage() {
  const navigate = useNavigate();
  const [selected, setSelected] = useState<ConversationSummary | null>(null);

  // Read-only transcript preview for the selected thread. Only fires once a row
  // is selected; the sidebar owns the summary list, this owns the transcript.
  const { data: transcript = [], isLoading: transcriptLoading } = useQuery({
    queryKey: [
      "conversation-transcript",
      selected?.agent_name,
      selected?.thread_id,
      selected?.deployment_id,
    ],
    queryFn: () =>
      listMemory(selected!.agent_name, {
        thread_id: selected!.thread_id,
        deployment_id: selected!.deployment_id ?? undefined,
        limit: 200,
      }),
    enabled: !!selected,
  });

  const turns = transcript.filter(
    (m: MemoryMessage) => m.role === "user" || m.role === "assistant",
  );

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-slate-900 mb-1">Conversations</h1>
      <p className="text-sm text-slate-500 mb-6">
        Your past conversations across every agent, sandbox and production. Open one
        to preview what's stored, then continue where you left off.
      </p>

      <div className="flex gap-6">
        {/* Left: shared cross-agent sidebar with env filter */}
        <div className="w-72 shrink-0">
          <ConversationSidebar
            scope={{ kind: "me" }}
            showEnvFilter
            activeThreadId={selected?.thread_id ?? null}
            onSelect={setSelected}
            // Cross-agent page has no single agent to start against, so "New
            // conversation" routes to the catalog to pick an agent/workflow to
            // chat with (was a no-op that just cleared the selection).
            onNew={() => navigate("/catalog")}
          />
        </div>

        {/* Right: read-only transcript preview + Continue */}
        <div className="flex-1 min-w-0">
          {!selected ? (
            <div className="card flex flex-col items-center justify-center py-20 text-center">
              <MessagesSquare size={40} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">Select a conversation</p>
              <p className="text-slate-400 text-sm mt-1">
                Pick a thread on the left to preview its transcript.
              </p>
            </div>
          ) : (
            <div className="card">
              <div className="flex items-center justify-between gap-3 mb-4 pb-3 border-b border-slate-100">
                <div className="min-w-0">
                  <p className="font-semibold text-slate-900 truncate">
                    {selected.title ?? "Untitled conversation"}
                  </p>
                  <p className="text-xs text-slate-400 inline-flex items-center gap-1.5">
                    <Database size={12} />
                    {selected.agent_name} · {selected.environment} · stored conversation
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    navigate(
                      `/agents/${selected.agent_name}/chat?session=${selected.thread_id}`,
                    )
                  }
                  className="btn-primary text-sm shrink-0"
                >
                  Continue <ArrowRight size={13} />
                </button>
              </div>

              {transcriptLoading ? (
                <p className="text-sm text-slate-400 px-1 py-8 text-center">
                  Loading transcript…
                </p>
              ) : turns.length === 0 ? (
                <p className="text-sm text-slate-400 px-1 py-8 text-center">
                  No messages in this conversation yet.
                </p>
              ) : (
                <div className="space-y-3">
                  {turns.map((m) => {
                    const isUser = m.role === "user";
                    return (
                      <div
                        key={m.id}
                        className={cn("flex gap-2.5", isUser && "flex-row-reverse")}
                      >
                        <div
                          className={cn(
                            "w-6 h-6 rounded-full flex items-center justify-center shrink-0",
                            isUser
                              ? "bg-slate-200"
                              : "bg-white border border-slate-200",
                          )}
                        >
                          {isUser ? (
                            <User size={12} className="text-slate-500" />
                          ) : (
                            <Bot size={12} className="text-emerald-600" />
                          )}
                        </div>
                        <div className={cn("max-w-md", isUser && "text-right")}>
                          <p className="text-[11px] text-slate-400 mb-0.5">
                            {isUser ? "You" : m.agent_name} ·{" "}
                            {formatTimestamp(m.created_at)}
                          </p>
                          <div
                            className={cn(
                              "rounded-lg px-3 py-2 text-sm inline-block text-left whitespace-pre-wrap",
                              isUser
                                ? "bg-blue-600 text-white"
                                : "bg-slate-50 border border-slate-200 text-slate-700",
                            )}
                          >
                            {m.content}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <p className="text-xs text-slate-400 mt-4 pt-3 border-t border-slate-100 inline-flex items-center gap-1.5">
                <MessagesSquare size={12} /> Read-only preview. Continue reopens this
                thread in the agent chat and picks up the same session.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
