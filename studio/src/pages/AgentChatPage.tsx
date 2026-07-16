import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, Loader2, Send, ShieldAlert, ExternalLink } from "lucide-react";
import {
  getAgent,
  getDeployments,
  startAgentChat,
  startDeploymentChat,
  getChatApprovalStatus,
  getSessionApprovals,
  SessionApproval,
} from "../api/registryApi";
import { getKeycloak } from "../lib/keycloak";
import ConversationApprovalPanel from "../components/chat/ConversationApprovalPanel";
import AttributedBubble from "../components/chat/AttributedBubble";
import { routeToken, openAuthorBubble } from "../lib/chatStream";

interface Message {
  role: "user" | "assistant";
  content: string;
  author?: string;
}

// Factory for a fresh assistant bubble, optionally attributed to `author`. Used
// both to seed the pending bubble and by the stream reducers (routeToken /
// openAuthorBubble) to open new bubbles as authored frames arrive.
const mk = (author?: string): Message => ({ role: "assistant", content: "", author });

interface PendingApproval {
  approvalId: string | null;
  toolName: string;
  risk: string;
  runId: string;
  args: Record<string, unknown>;
}

// Shape of the `approval_requested` SSE payload (fields the pod/registry emit).
interface ApprovalEvent {
  approval_id?: string;
  tool?: string;
  tool_name?: string;
  risk?: string;
  risk_level?: string;
  args?: Record<string, unknown>;
  reasoning?: string | null;
}

// How often the chat polls the HITL console for a decision (production path).
const APPROVAL_POLL_MS = 3000;

export default function AgentChatPage() {
  const { name, depId } = useParams<{ name: string; depId?: string }>();

  const { data: agent } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

  // Resolve the deployment's environment to pick the approval model:
  //  - sandbox  → developer self-approves in the right-side panel
  //  - production → wait for a reviewer in the HITL console
  // A deployment chat with no depId is a playground/sandbox session → self-approve.
  const { data: deployments } = useQuery({
    queryKey: ["agent-deployments", name],
    queryFn: () => getDeployments(name!),
    enabled: !!name && !!depId,
  });
  const deployment = deployments?.find((d) => d.id === depId);
  const isSandbox = depId ? deployment?.environment !== "production" : true;

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID());
  // Production path: single waiting banner. Sandbox path: self-approve panel list.
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [sandboxApprovals, setSandboxApprovals] = useState<SessionApproval[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Holds onApprovalRequested so connectResumeStream can surface a re-interrupt
  // during resume without a useCallback dependency cycle.
  const reinterruptRef = useRef<(data: ApprovalEvent, runId: string) => void>(() => {});

  const awaitingApproval = !!pendingApproval || sandboxApprovals.length > 0;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const clearPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      esRef.current?.close();
      clearPoll();
    };
  }, [clearPoll]);

  // Reconnect the resume stream after an approval decision (sandbox or console).
  const connectResumeStream = useCallback((runId: string) => {
    if (!name) return;
    setPendingApproval(null);
    setSandboxApprovals([]);
    setIsStreaming(true);

    const kc = getKeycloak();
    const token = kc?.token;
    const resumeUrl = token
      ? `/api/v1/agents/${name}/chat/${runId}/resume-stream?token=${encodeURIComponent(token)}`
      : `/api/v1/agents/${name}/chat/${runId}/resume-stream`;

    const source = new EventSource(resumeUrl);
    esRef.current = source;

    setMessages((prev) => [...prev, mk(name)]);

    source.onmessage = (event) => {
      try {
        const d = JSON.parse(event.data);
        if (d.type === "agent_start") {
          setMessages((prev) => openAuthorBubble(prev, d.author, mk));
        } else if (d.type === "token") {
          setMessages((prev) => routeToken(prev, d.author, d.content, mk));
        } else if (d.type === "done") {
          source.close();
          esRef.current = null;
          setIsStreaming(false);
        } else if (d.type === "error") {
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, content: last.content + `\n\n[Error: ${d.message}]` };
            return copy;
          });
          source.close();
          esRef.current = null;
          setIsStreaming(false);
        } else if (d.type === "approval_requested") {
          // A later-turn tool call re-interrupted DURING resume. Surface the next
          // approval (re-open the panel / re-arm the poll) instead of hanging.
          // Via a ref to avoid a useCallback dependency cycle.
          source.close();
          esRef.current = null;
          reinterruptRef.current(d, runId);
        }
      } catch {
        // ignore parse errors
      }
    };

    source.onerror = () => {
      source.close();
      esRef.current = null;
      setIsStreaming(false);
    };
  }, [name]);

  // Production path: poll the console for a decision, then auto-resume.
  const startApprovalPolling = useCallback((runId: string) => {
    if (!name) return;
    clearPoll();
    pollRef.current = setInterval(async () => {
      try {
        const s = await getChatApprovalStatus(name, runId);
        if (s.decided) {
          clearPoll();
          if (s.status === "rejected") {
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              if (last?.role === "assistant") {
                copy[copy.length - 1] = {
                  ...last,
                  content: "Tool request was denied by a reviewer. Responding without it…",
                };
              }
              return copy;
            });
          }
          connectResumeStream(runId);
        }
      } catch {
        // transient — keep polling
      }
    }, APPROVAL_POLL_MS);
  }, [name, clearPoll, connectResumeStream]);

  // Sandbox path: developer decided in the panel → resume the graph.
  const handleSandboxDecided = useCallback(
    (runId: string, decision: "approved" | "denied") => {
      if (decision === "denied") {
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") {
            copy[copy.length - 1] = {
              ...last,
              content: "You denied the tool call. Responding without it…",
            };
          }
          return copy;
        });
      }
      connectResumeStream(runId);
    },
    [connectResumeStream]
  );

  const onApprovalRequested = useCallback(
    async (data: ApprovalEvent, runId: string) => {
      const toolName = data.tool || data.tool_name || "unknown";
      const risk = data.risk || data.risk_level || "high";
      setIsStreaming(false);

      if (isSandbox) {
        // Seed the panel from the event so there's no empty flash, then reconcile
        // against the session endpoint (forward-proof for multiple/history).
        const seed: SessionApproval = {
          approval_id: data.approval_id ?? "",
          run_id: runId,
          status: "pending",
          tool: toolName,
          args: data.args || {},
          risk,
          reasoning: data.reasoning ?? null,
          requested_by: null,
          requested_by_team: null,
          context: "sandbox",
          created_at: null,
          decided: false,
        };
        setSandboxApprovals([seed]);
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          copy[copy.length - 1] = {
            ...last,
            content: last.content || `Approve the "${toolName}" tool call to continue →`,
          };
          return copy;
        });
        // Show ONLY the current approval (the one this event announced), enriched
        // with provenance (WHO/WHY) from the DB. We intentionally do NOT show the
        // whole session's pending list — the tool node re-runs on resume and can
        // leave benign pending "orphan" rows from prior turns, which would pile up
        // in the panel. One approval at a time; the resume chain surfaces the next.
        try {
          const list = await getSessionApprovals(name!, sessionId);
          const current = list.find(
            (a) => a.approval_id === data.approval_id && a.status === "pending"
          );
          if (current) setSandboxApprovals([current]);
        } catch {
          // keep the seed row
        }
      } else {
        // Production: wait for a reviewer in the console; poll to auto-resume.
        setPendingApproval({
          approvalId: data.approval_id ?? null,
          toolName,
          risk,
          runId,
          args: data.args || {},
        });
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          copy[copy.length - 1] = {
            ...last,
            content: last.content || `Waiting for a reviewer to approve the "${toolName}" call…`,
          };
          return copy;
        });
        startApprovalPolling(runId);
      }
    },
    [isSandbox, name, sessionId, startApprovalPolling]
  );

  // Keep the ref current so connectResumeStream can surface a re-interrupt.
  useEffect(() => {
    reinterruptRef.current = onApprovalRequested;
  }, [onApprovalRequested]);

  const sendMessage = async () => {
    if (!input.trim() || isStreaming || awaitingApproval || !name) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setIsStreaming(true);

    try {
      const res = depId
        ? await startDeploymentChat(name, depId, { message: userMsg, session_id: sessionId })
        : await startAgentChat(name, { message: userMsg, session_id: sessionId, context: "playground" });

      setMessages((prev) => [...prev, mk(name)]);

      const kc = getKeycloak();
      if (kc?.authenticated) {
        await kc.updateToken(10);
      }
      const freshToken = kc?.token;

      const streamUrl = freshToken
        ? `${res.stream_url}?token=${encodeURIComponent(freshToken)}`
        : res.stream_url;

      const source = new EventSource(streamUrl);
      esRef.current = source;

      source.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "agent_start") {
            setMessages((prev) => openAuthorBubble(prev, data.author, mk));
          } else if (data.type === "token") {
            setMessages((prev) => routeToken(prev, data.author, data.content, mk));
          } else if (data.type === "approval_requested") {
            source.close();
            esRef.current = null;
            onApprovalRequested(data, res.run_id);
          } else if (data.type === "done") {
            source.close();
            esRef.current = null;
            setIsStreaming(false);
          } else if (data.type === "error") {
            source.close();
            esRef.current = null;
            setIsStreaming(false);
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = { ...last, content: data.message || "Agent error" };
              return copy;
            });
          }
        } catch {
          // ignore parse errors in stream
        }
      };

      source.onerror = () => {
        source.close();
        esRef.current = null;
        setIsStreaming(false);
      };
    } catch {
      setIsStreaming(false);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Error: failed to start chat. Check that this agent is deployed.",
        },
      ]);
    }
  };

  return (
    <div className="flex h-screen bg-white">
      {/* Chat column */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="border-b border-slate-200 px-6 py-3 flex items-center gap-3 shrink-0">
          <Link to={depId ? `/agents/${name}/d/${depId}` : "/my-agents"} className="text-slate-400 hover:text-slate-600">
            <ArrowLeft size={16} />
          </Link>
          <Bot size={18} className="text-blue-600 shrink-0" />
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-semibold text-slate-900 truncate">
              {agent?.name ?? name}
            </h1>
            {agent?.description && (
              <p className="text-xs text-slate-400 truncate">{agent.description}</p>
            )}
          </div>
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
            Live
          </span>
        </div>

        {/* Production waiting banner — sandbox uses the right-side panel instead */}
        {pendingApproval && (
          <div
            className="bg-amber-50 border-b border-amber-200 px-6 py-4 shrink-0"
            data-testid="hitl-waiting-banner"
          >
            <div className="flex items-start gap-3">
              <ShieldAlert size={20} className="text-amber-600 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-amber-800">Waiting for approval</p>
                <p className="text-sm text-amber-700 mt-1">
                  The agent wants to call{" "}
                  <span className="font-mono font-medium" data-testid="hitl-tool-name">
                    {pendingApproval.toolName}
                  </span>
                  {pendingApproval.risk && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded text-xs font-medium bg-red-100 text-red-700 uppercase">
                      {pendingApproval.risk} risk
                    </span>
                  )}
                  . A reviewer must approve it in the approval console — this chat
                  will continue automatically once they decide.
                </p>
                {Object.keys(pendingApproval.args).length > 0 && (
                  <pre className="mt-2 p-2 rounded bg-amber-100 text-xs text-amber-900 overflow-x-auto max-w-full">
                    {JSON.stringify(pendingApproval.args, null, 2)}
                  </pre>
                )}
                <div className="flex items-center gap-3 mt-3">
                  <Link
                    to="/hitl"
                    target="_blank"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 text-white text-xs font-medium rounded-md hover:bg-amber-700 transition-colors"
                  >
                    Open approval console
                    <ExternalLink size={12} />
                  </Link>
                  <span className="inline-flex items-center gap-1.5 text-xs text-amber-600">
                    <Loader2 size={12} className="animate-spin" />
                    Watching for a decision…
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center h-full text-center text-slate-400 gap-2">
              <Bot size={32} className="text-slate-300" />
              <p className="text-sm font-medium">Start a conversation</p>
              {agent?.description && <p className="text-xs max-w-xs">{agent.description}</p>}
            </div>
          )}
          {messages.map((m, i) => (
            <AttributedBubble
              key={i}
              role={m.role}
              content={m.content}
              author={m.author}
              showLabel={false}
              streaming={m.role === "assistant" && isStreaming && i === messages.length - 1}
            />
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t border-slate-200 px-6 py-4 shrink-0">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              sendMessage();
            }}
            className="flex gap-2"
          >
            <input
              className="flex-1 border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-slate-50 disabled:text-slate-400"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isStreaming || awaitingApproval}
              placeholder={
                isStreaming
                  ? "Waiting for response…"
                  : awaitingApproval
                    ? "Awaiting tool approval…"
                    : "Message…"
              }
            />
            <button
              type="submit"
              disabled={isStreaming || !input.trim() || awaitingApproval}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1"
            >
              {isStreaming ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            </button>
          </form>
        </div>
      </div>

      {/* Sandbox self-approve panel (right side) */}
      {sandboxApprovals.length > 0 && (
        <ConversationApprovalPanel
          approvals={sandboxApprovals}
          onDecided={handleSandboxDecided}
          onClose={() => setSandboxApprovals([])}
        />
      )}
    </div>
  );
}
