import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listMemory, deleteMemoryThread, clearAgentMemory, MemoryMessage } from "../../api/registryApi";
import { useState } from "react";

interface Props {
  agentName: string;
}

export default function MemoryTab({ agentName }: Props) {
  const queryClient = useQueryClient();
  const [selectedThread, setSelectedThread] = useState<string | null>(null);

  const { data: messages = [], isLoading } = useQuery({
    queryKey: ["memory", agentName, selectedThread],
    queryFn: () =>
      listMemory(agentName, {
        thread_id: selectedThread ?? undefined,
        limit: 100,
      }),
  });

  const deleteThread = useMutation({
    mutationFn: (threadId: string) => deleteMemoryThread(agentName, threadId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", agentName] });
      setSelectedThread(null);
    },
  });

  const clearAll = useMutation({
    mutationFn: () => clearAgentMemory(agentName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", agentName] });
      setSelectedThread(null);
    },
  });

  const threads = [...new Set(messages.map((m) => m.thread_id))];

  if (isLoading) {
    return <div className="p-6 text-gray-500">Loading memory...</div>;
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium text-gray-900">Conversation Memory</h3>
        <button
          onClick={() => {
            if (confirm("Clear all memory for this agent? This cannot be undone.")) {
              clearAll.mutate();
            }
          }}
          disabled={messages.length === 0}
          className="px-3 py-1.5 text-sm bg-red-50 text-red-700 rounded hover:bg-red-100 disabled:opacity-50"
        >
          Clear All
        </button>
      </div>

      {messages.length === 0 && !selectedThread && (
        <p className="text-gray-500 text-sm">No memory stored for this agent.</p>
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

      {selectedThread && (
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <span>Thread: {selectedThread}</span>
          <button
            onClick={() => deleteThread.mutate(selectedThread)}
            className="text-red-600 hover:text-red-800 underline text-xs"
          >
            Delete thread
          </button>
        </div>
      )}

      <div className="space-y-2 max-h-[600px] overflow-y-auto">
        {messages.map((msg) => (
          <div
            key={msg.id}
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
              <span className="text-xs text-gray-400">
                {new Date(msg.created_at).toLocaleString()}
              </span>
            </div>
            <p className="text-gray-800 whitespace-pre-wrap">{msg.content}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
