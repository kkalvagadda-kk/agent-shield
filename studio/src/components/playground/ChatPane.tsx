import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, ExternalLink, Loader2, Send, ThumbsDown, ThumbsUp } from "lucide-react";
import { getRunTrace, startPlaygroundRun, submitRunFeedback } from "../../api/playgroundApi";
import { toast } from "sonner";
import TraceDrawer from "./TraceDrawer";
import SafetyDetails, { SafetyResult } from "./SafetyDetails";
import AttributedBubble from "../chat/AttributedBubble";
import { type Citation, parseKnowledgeCitations } from "../../lib/chatStream";

export interface Message {
  role: "user" | "assistant";
  content: string;
  author?: string;
  chips?: { type: "tool_start" | "tool_end"; label: string; id?: string }[];
  safetyBlock?: SafetyResult;
  // POC-4: {source, kb}[] parsed from a knowledge_search tool_call_end result.
  citations?: Citation[];
}

interface Props {
  agentName: string | null;
  resumeStreamUrl: string | null;
  onApprovalRequested: (
    approvalId: string,
    toolName: string,
    riskLevel: string,
    args: Record<string, unknown>,
    reasoning?: string | null,
    requestedBy?: string | null,
    requestedByTeam?: string | null
  ) => void;
  onResumeComplete: () => void;
  onTraceEvent: (event: { ts: string; event: string; content?: string; tool_name?: string; result?: string }) => void;
  // POC-5 History: optional seed of a past thread's transcript (plain user/assistant
  // bubbles). Rendered on mount only — the parent remounts ChatPane (via `key`) when
  // resuming a conversation, so all live streaming / HITL / trace behavior below is
  // untouched. Omitted → a fresh empty chat, exactly as before.
  initialMessages?: Message[];
  // Reports the in-flight run state up to the parent so it can block History
  // select/New while streaming (a remount mid-stream would drop the SSE connection).
  onRunningChange?: (running: boolean) => void;
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

export default function ChatPane({ agentName, resumeStreamUrl, onApprovalRequested, onResumeComplete, onTraceEvent, initialMessages, onRunningChange }: Props) {
  const [messages, setMessages] = useState<Message[]>(() => initialMessages ?? []);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [traceUrl, setTraceUrl] = useState<string | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [showTraceDrawer, setShowTraceDrawer] = useState(false);
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

  // Surface the run state to the parent (PlaygroundPage) so History select/New can
  // be disabled while a run is streaming. Setter identity is stable, so this only
  // fires on an actual running-state change.
  useEffect(() => {
    onRunningChange?.(running);
  }, [running, onRunningChange]);

  const connectStream = useCallback((
    url: string,
    runId: string,
    onDone?: () => void,
  ) => {
    esRef.current?.close();
    const es = new EventSource(url);
    esRef.current = es;
    setRunning(true);

    es.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data) as Record<string, unknown>;
        const event = payload.event as string;
        const ts = new Date().toISOString();

        if (event && event !== "message" && event !== "text_delta") {
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
              // Playground stream is single-agent: attribute the bubble to the
              // selected agent (prop). Raw runner events carry no `author`.
              updated[updated.length - 1] = { ...last, content: last.content + content, author: agentName ?? undefined };
            }
            return updated;
          });
        } else if (event === "tool_call_start") {
          const toolName = (payload.tool_name as string) ?? "tool";
          const callId = (payload.tool_call_id as string) ?? undefined;
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              // Dedupe by tool_call_id: the tool node re-runs on resume and
              // re-emits tool_call_start for the same call — don't add a 2nd chip.
              if (callId && (last.chips ?? []).some((c) => c.id === callId)) {
                return updated;
              }
              updated[updated.length - 1] = {
                ...last,
                chips: [...(last.chips ?? []), { type: "tool_start", label: `Calling ${toolName}…`, id: callId }],
              };
            }
            return updated;
          });
        } else if (event === "tool_call_end") {
          const toolName = (payload.tool_name as string) ?? "tool";
          const result = (payload.result as string) ?? "done";
          const callId = (payload.tool_call_id as string) ?? undefined;
          // POC-4: a knowledge_search result carries the {source, kb}[] the
          // AttributedBubble citation row renders (F-4 — frontend-only wiring).
          const citations =
            toolName === "knowledge_search" ? parseKnowledgeCitations(result) : [];
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last.role === "assistant") {
              const chips = [...(last.chips ?? [])];
              // Match the chip for this call id if present, else the latest open start.
              let idx = callId ? chips.findIndex((c) => c.id === callId && c.type === "tool_start") : -1;
              if (idx < 0) {
                for (let i = chips.length - 1; i >= 0; i--) { if (chips[i].type === "tool_start") { idx = i; break; } }
              }
              if (idx >= 0) chips[idx] = { type: "tool_end", label: `${toolName}: ${String(result).slice(0, 40)}`, id: callId };
              updated[updated.length - 1] = {
                ...last,
                chips,
                ...(citations.length > 0
                  ? { citations: [...(last.citations ?? []), ...citations] }
                  : {}),
              };
            }
            return updated;
          });
        } else if (event === "approval_requested") {
          es.close();
          esRef.current = null;
          onApprovalRequested(
            (payload.approval_id as string) ?? "",
            (payload.tool_name as string) ?? "",
            (payload.risk_level as string) ?? "high",
            (payload.args as Record<string, unknown>) ?? {},
            (payload.reasoning as string | null) ?? null,
            (payload.requested_by as string | null) ?? null,
            (payload.requested_by_team as string | null) ?? null
          );
        } else if (event === "error" && payload.type === "safety_blocked") {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              updated[updated.length - 1] = {
                ...last,
                content: last.content || "Message blocked by safety scan.",
                safetyBlock: {
                  reason: (payload.reason as string) || "Input blocked by safety scanner",
                  type: "safety_blocked",
                  scanners: payload.scanners as SafetyResult["scanners"],
                },
              };
            }
            return updated;
          });
          es.close();
          esRef.current = null;
          setRunning(false);
          onDone?.();
        } else if (event === "error") {
          const errorMsg = (payload.reason as string) || (payload.message as string) || "Agent error";
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              updated[updated.length - 1] = { ...last, content: errorMsg };
            }
            return updated;
          });
          es.close();
          esRef.current = null;
          setRunning(false);
          onDone?.();
        } else if (event === "done") {
          es.close();
          esRef.current = null;
          setRunning(false);
          getRunTrace(runId).then((t) => {
            if (t.trace_url) setTraceUrl(t.trace_url);
            if (t.trace_id) setTraceId(t.trace_id);
          }).catch(() => {});
          onDone?.();
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
      onDone?.();
    };
  }, [onApprovalRequested, onTraceEvent]);

  // Connect to resume stream when URL is provided (after HITL approve/deny)
  useEffect(() => {
    if (!resumeStreamUrl || !currentRunId) return;
    connectStream(resumeStreamUrl, currentRunId, onResumeComplete);
  }, [resumeStreamUrl, currentRunId, connectStream, onResumeComplete]);

  const handleSend = async () => {
    if (!input.trim() || !agentName || running) return;

    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setCurrentRunId(null);
    setTraceUrl(null);
    setFeedbackGiven(null);

    setMessages((prev) => [...prev, { role: "assistant", content: "", chips: [] }]);

    try {
      const { run_id, stream_url } = await startPlaygroundRun({
        agent_name: agentName,
        input_message: userMsg,
      });

      setCurrentRunId(run_id);
      connectStream(stream_url, run_id);
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
          <AttributedBubble
            key={i}
            role={msg.role}
            content={msg.content}
            author={msg.author}
            showLabel={false}
            citations={msg.citations}
          >
            {msg.chips && msg.chips.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
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
            {!msg.content && msg.role === "assistant" && running && i === messages.length - 1 && (
              <Loader2 size={14} className="animate-spin text-slate-400" />
            )}
            {msg.safetyBlock && <SafetyDetails result={msg.safetyBlock} />}
          </AttributedBubble>
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
          {!traceUrl && traceId && (
            <button
              onClick={() => setShowTraceDrawer(true)}
              className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800"
            >
              <Eye size={12} />
              View Trace
            </button>
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

      {showTraceDrawer && traceId && (
        <TraceDrawer traceId={traceId} onClose={() => setShowTraceDrawer(false)} />
      )}
    </div>
  );
}
