import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { listWorkflowMemory, type MemoryMessage } from "../../api/registryApi";

interface Props {
  workflowId: string;
  deploymentId: string;
}

/**
 * Workflow deployment Memory tab (POC-5 ledger, G2). Mirror of the agent MemoryTab
 * but scoped to a WORKFLOW: a workflow's transcript is authored by its members
 * (member agent_name, NULL user_id), so entries resolve server-side through the
 * workflow's parent runs (GET /workflows/{id}/memory) rather than by the workflow's
 * own name — which is exactly why the old `MemoryTab(agentName=workflow.name)` matched
 * nothing and the tab was empty. Read-only: member rows aren't agent-owned, so there
 * is no Clear-All / Delete-thread (those are the agent-scoped mutations MemoryTab owns
 * that don't apply here). Per-deployment scoping is deferred (playground/builder runs
 * carry workflow_deployment_id=NULL) — the list is scoped by workflow + owner.
 */
export default function WorkflowMemoryTab({ workflowId }: Props) {
  const [selectedThread, setSelectedThread] = useState<string | null>(null);

  const { data: messages = [], isLoading } = useQuery({
    queryKey: ["workflow-memory", workflowId, selectedThread],
    queryFn: () =>
      listWorkflowMemory(workflowId, {
        thread_id: selectedThread ?? undefined,
        limit: 100,
      }),
  });

  const threads = [...new Set(messages.map((m) => m.thread_id))];

  if (isLoading) {
    return <div className="p-6 text-gray-500">Loading memory...</div>;
  }

  return (
    <div className="p-6 space-y-4" data-testid="workflow-memory-tab">
      <h3 className="text-lg font-medium text-gray-900">Conversation Memory</h3>

      {messages.length === 0 && !selectedThread && (
        <p className="text-gray-500 text-sm">No memory stored for this workflow yet.</p>
      )}

      {threads.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setSelectedThread(null)}
            className={`px-3 py-1 text-xs rounded-full border ${
              !selectedThread ? "bg-blue-100 border-blue-300 text-blue-800" : "bg-gray-50 border-gray-200"
            }`}
          >
            All threads
          </button>
          {threads.map((t) => (
            <button
              key={t}
              onClick={() => setSelectedThread(t)}
              className={`px-3 py-1 text-xs rounded-full border ${
                selectedThread === t ? "bg-blue-100 border-blue-300 text-blue-800" : "bg-gray-50 border-gray-200"
              }`}
            >
              {t.slice(0, 8)}...
            </button>
          ))}
        </div>
      )}

      <div className="space-y-2 max-h-[600px] overflow-y-auto">
        {messages.map((msg: MemoryMessage) => (
          <div
            key={msg.id ?? `${msg.thread_id}-${msg.message_index}`}
            className={`p-3 rounded-lg text-sm ${
              msg.role === "user"
                ? "bg-blue-50 border border-blue-100"
                : msg.role === "assistant"
                ? "bg-gray-50 border border-gray-100"
                : "bg-yellow-50 border border-yellow-100"
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              <span className="font-medium text-xs uppercase text-gray-500">{msg.role}</span>
              {msg.role === "assistant" && msg.agent_name && (
                <span className="text-xs text-indigo-600 font-medium">{msg.agent_name}</span>
              )}
              {msg.created_at && (
                <span className="text-xs text-gray-400">
                  {new Date(msg.created_at).toLocaleString()}
                </span>
              )}
            </div>
            <p className="text-gray-800 whitespace-pre-wrap">{msg.content}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
