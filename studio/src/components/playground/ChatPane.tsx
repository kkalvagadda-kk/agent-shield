import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, Send, ThumbsDown, ThumbsUp } from "lucide-react";
import { getRunTrace, startPlaygroundRun, submitRunFeedback } from "../../api/playgroundApi";
import { toast } from "sonner";

interface Message {
  role: "user" | "assistant";
  content: string;
  chips?: { type: "tool_start" | "tool_end"; label: string }[];
}

interface Props {
  agentName: string | null;
  onApprovalRequested: (
    approvalId: string,
    toolName: string,
    riskLevel: string,
    args: Record<string, unknown>
  ) => void;
  onTraceEvent: (event: { ts: string; event: string; content?: string; tool_name?: string; result?: string }) => void;
}

function coerceToString(val: unknown): string {
  if (val == null) return "";
  if (typeof val === "string") return val;
  if (Array.isArray(val)) {
    return val.map((b: Record<string, unknown>) => (b as { text?: string }).text ?? JSON.stringify(b)).join("");
  }
  if (typeof val === "object") {
    return (val as { text?: string }).text ?? JSON.stringify(val);
  }
  return String(val);
}

export default function ChatPane({ agentName, onApprovalRequested, onTraceEvent }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [traceUrl, setTraceUrl] = useState<string | null>(null);
  const [feedbackGiven, setFeedbackGiven] = useState<1 | -1 | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return () => {
      esRef.current?.close();
    };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || !agentName || running) return;

    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setRunning(true);
    setCurrentRunId(null);
    setTraceUrl(null);
    setFeedbackGiven(null);

    // Add empty assistant message that will grow as SSE events arrive
    setMessages((prev) => [...prev, { role: "assistant", content: "", chips: [] }]);

    try {
      const { run_id, stream_url } = await startPlaygroundRun({
        agent_name: agentName,
        input_message: userMsg,
      });

      const es = new EventSource(stream_url);
      esRef.current = es;

      es.onmessage = (e) => {
        try {
          const payload = JSON.parse(e.data) as Record<string, unknown>;
          const event = payload.event as string;
          const ts = new Date().toISOString();

          if (event && event !== "message") {
            const traceContent = payload.content != null ? coerceToString(payload.content) : undefined;
            const traceTool = payload.tool_name != null ? String(payload.tool_name) : undefined;
            const traceResult = payload.result != null ? coerceToString(payload.result) : undefined;
            onTraceEvent({ ts, event, content: traceContent, tool_name: traceTool, result: traceResult });
          }

          if (event === "text_delta") {
            const content = coerceToString(payload.content);
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + content,
                };
              }
              return updated;
            });
          } else if (event === "tool_call_start") {
            const toolName = (payload.tool_name as string) ?? "tool";
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last.role === "assistant") {
                updated[updated.length - 1] = {
                  ...last,
                  chips: [...(last.chips ?? []), { type: "tool_start", label: `Calling ${toolName}…` }],
                };
              }
              return updated;
            });
          } else if (event === "tool_call_end") {
            const toolName = (payload.tool_name as string) ?? "tool";
            const result = (payload.result as string) ?? "done";
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last.role === "assistant") {
                const chips = [...(last.chips ?? [])];
                let idx = -1;
                for (let i = chips.length - 1; i >= 0; i--) { if (chips[i].type === "tool_start") { idx = i; break; } }
                if (idx >= 0) chips[idx] = { type: "tool_end", label: `${toolName}: ${String(result).slice(0, 40)}` };
                updated[updated.length - 1] = { ...last, chips };
              }
              return updated;
            });
          } else if (event === "approval_requested") {
            onApprovalRequested(
              (payload.approval_id as string) ?? "",
              (payload.tool_name as string) ?? "",
              (payload.risk_level as string) ?? "high",
              (payload.args as Record<string, unknown>) ?? {}
            );
          } else if (event === "done") {
            es.close();
            esRef.current = null;
            setRunning(false);
            // Fetch trace URL after completion
            getRunTrace(run_id).then((t) => {
              if (t.trace_url) setTraceUrl(t.trace_url);
            }).catch(() => {});
          }
        } catch {
          // ignore parse errors in stream
        }
      };

      es.onerror = () => {
        es.close();
        esRef.current = null;
        setRunning(false);
        toast.error("Stream connection lost.");
      };

      setCurrentRunId(run_id);
    } catch (err) {
      setRunning(false);
      toast.error((err as Error)?.message ?? "Failed to start run.");
    }
  };

  if (!agentName) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-400">
        <div className="text-center">
          <p className="text-sm font-medium">No agent selected</p>
          <p className="text-xs mt-1">Pick an agent from the left panel to start chatting.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Message list */}
      <div ref={scrollRef} className="flex-1 overflow-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-slate-300 text-sm">
            Send a message to start a playground run.
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[70%] rounded-lg px-3 py-2 text-sm ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-slate-100 text-slate-800"
              }`}
            >
              {msg.chips && msg.chips.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {msg.chips.map((chip, ci) => (
                    <span
                      key={ci}
                      className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-mono ${
                        chip.type === "tool_end"
                          ? "bg-green-100 text-green-700"
                          : "bg-blue-100 text-blue-700"
                      }`}
                    >
                      {chip.label}
                    </span>
                  ))}
                </div>
              )}
              {msg.content || (msg.role === "assistant" && running && i === messages.length - 1 ? (
                <Loader2 size={14} className="animate-spin text-slate-400" />
              ) : null)}
            </div>
          </div>
        ))}
      </div>

      {/* Feedback + trace bar (shown after run completes) */}
      {!running && currentRunId && (
        <div className="border-t border-slate-100 px-4 py-2 flex items-center gap-3">
          <div className="flex items-center gap-1">
            <button
              onClick={async () => {
                await submitRunFeedback(currentRunId, 1);
                setFeedbackGiven(1);
                toast.success("Thanks for your feedback!");
              }}
              disabled={feedbackGiven !== null}
              className={`p-1 rounded hover:bg-green-50 ${feedbackGiven === 1 ? "text-green-600" : "text-slate-400"}`}
              title="Thumbs up"
            >
              <ThumbsUp size={14} />
            </button>
            <button
              onClick={async () => {
                await submitRunFeedback(currentRunId, -1);
                setFeedbackGiven(-1);
                toast.success("Thanks for your feedback!");
              }}
              disabled={feedbackGiven !== null}
              className={`p-1 rounded hover:bg-red-50 ${feedbackGiven === -1 ? "text-red-600" : "text-slate-400"}`}
              title="Thumbs down"
            >
              <ThumbsDown size={14} />
            </button>
          </div>
          {traceUrl && (
            <a
              href={traceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800"
            >
              <ExternalLink size={12} />
              View Trace
            </a>
          )}
        </div>
      )}

      {/* Input bar */}
      <div className="border-t border-slate-200 p-3 flex gap-2">
        <input
          className="input flex-1 text-sm"
          placeholder={`Message ${agentName}…`}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
          disabled={running}
        />
        <button
          onClick={handleSend}
          disabled={running || !input.trim()}
          className="btn-primary px-3 py-2 shrink-0"
        >
          {running ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </div>
    </div>
  );
}
