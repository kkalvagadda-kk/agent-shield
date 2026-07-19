import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, GitBranch, Loader2, Send, RefreshCw } from "lucide-react";
import {
  getCompositeWorkflow,
  getWorkflowRunTree,
  workflowRunStreamUrl,
  listWorkflowMemory,
  listPendingApprovals,
  type SessionApproval,
  type WorkflowStreamFrame,
} from "../api/registryApi";
import { getKeycloak } from "../lib/keycloak";
import AttributedBubble from "../components/chat/AttributedBubble";
import ConversationApprovalPanel from "../components/chat/ConversationApprovalPanel";
import {
  routeToken,
  openAuthorBubble,
  attachToolCall,
  attachRationale,
  type ToolCall,
} from "../lib/chatStream";

// ---------------------------------------------------------------------------
// WorkflowChatPage — the "chat endpoint + icon" surface for a workflow, at parity
// with an ephemeral agent's chat. Streams POST /workflows/{id}/runs/stream and
// renders each member's turn as its own attributed bubble.
//
// HITL: when a member trips a high-risk approval gate the workflow PARKS (both
// reactive and durable — the reactive fail-closed was reverted). We show the same
// INLINE self-approve panel the single-agent chat uses; on decide, the backend
// (playground decide → _resume_and_advance) resumes the member pod and advances the
// orchestration, and we poll the run tree to render the resumed continuation.
// ---------------------------------------------------------------------------

interface Message {
  role: "user" | "assistant";
  content: string;
  author?: string; // member agent name; undefined → unlabeled
  toolCalls?: ToolCall[];
  rationale?: string | null;
}

const mk = (author?: string): Message => ({ role: "assistant", content: "", author });

export default function WorkflowChatPage() {
  const { id, depId } = useParams<{ id: string; depId?: string }>();
  const [searchParams] = useSearchParams();

  const { data: workflow } = useQuery({
    queryKey: ["composite-workflow", id],
    queryFn: () => getCompositeWorkflow(id!),
    enabled: !!id,
  });

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<SessionApproval | null>(null);
  const [sessionId, setSessionId] = useState(
    () => searchParams.get("session") ?? crypto.randomUUID(),
  );
  const parkedRunIdRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingApproval, resuming]);

  const backTo = depId ? `/workflows/${id}/d/${depId}` : `/workflows/${id}`;
  const awaitingApproval = !!pendingApproval;

  const resetConversation = () => {
    setMessages([]);
    setPendingApproval(null);
    parkedRunIdRef.current = null;
    setSessionId(crypto.randomUUID());
  };

  // POC-5 (G1): rehydrate a past workflow session's transcript. Maps memory rows to
  // plain attributed bubbles (assistant rows keep their member agent_name as author;
  // user rows are unlabeled) — rich slots (tool chips / rationale) are only rendered on
  // the live stream, matching AgentChatPage's seed behavior.
  const seedFromThread = useCallback(async (workflowId: string, threadId: string) => {
    const rows = await listWorkflowMemory(workflowId, { thread_id: threadId, limit: 200 });
    setSessionId(threadId);
    setMessages(
      rows
        .filter((r) => r.role === "user" || r.role === "assistant")
        .map((r) => ({
          role: r.role as "user" | "assistant",
          content: r.content,
          author: r.role === "assistant" ? r.agent_name : undefined,
        })),
    );
  }, []);

  // On direct entry with `?session=<threadId>` (e.g. a Conversations row click), replay
  // that past session once on mount. Previously the workflow chat opened on the empty
  // composer even though it continued the correct session — the G1 gap.
  useEffect(() => {
    const seed = searchParams.get("session");
    if (seed && id) {
      seedFromThread(id, seed);
    }
    // Mount-only deep-link seed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // After an inline decision, the backend resumes the member + advances the
  // orchestration (fire-and-forget). Poll the run tree until the parent run is
  // terminal, then render the resumed members' output as attributed bubbles.
  const pollResumedResult = async (runId: string) => {
    if (!id) return;
    setResuming(true);
    try {
      for (let i = 0; i < 90; i++) {
        await new Promise((r) => setTimeout(r, 4000));
        let tree;
        try {
          tree = await getWorkflowRunTree(id, runId);
        } catch {
          continue;
        }
        const status = tree.parent?.status;
        if (status === "awaiting_approval") {
          // The resumed member RE-PARKED on a 2nd approval gate (the registry re-park
          // fix — a member can trip more than one gate in a turn). Surface it inline:
          // find the still-pending playground approval on the parked child's thread and
          // re-render the ConversationApprovalPanel. onDecided re-enters
          // pollResumedResult(runId), so the loop closes itself once no gate remains.
          const parked = (tree.children ?? []).find(
            (c) => c.status === "awaiting_approval" && c.thread_id,
          );
          if (parked?.thread_id) {
            let approvals: Awaited<ReturnType<typeof listPendingApprovals>> = [];
            try {
              approvals = await listPendingApprovals(undefined, "playground");
            } catch {
              /* transient — retry on the next poll */
            }
            const next = approvals.find((a) => a.thread_id === parked.thread_id);
            if (next) {
              setResuming(false);
              setPendingApproval({
                approval_id: next.id,
                run_id: runId,
                status: "pending",
                tool: next.tool_name,
                args: next.tool_args,
                risk: next.risk_level,
                reasoning: next.thread_context_snippet ?? null,
                requested_by: null,
                requested_by_team: next.team ?? null,
                context: next.context,
                created_at: next.created_at,
                decided: false,
              });
              return; // the panel's onDecided re-enters pollResumedResult(runId)
            }
          }
          continue; // still resuming — keep polling
        }
        if (status === "completed" || status === "failed") {
          // Render every member's final output as its own attributed bubble, then
          // the parent's final answer. (Live pre-approval bubbles stay above; this
          // appends the resumed continuation the poll observed.)
          setMessages((prev) => {
            const next = [...prev];
            for (const child of tree.children ?? []) {
              next.push({
                role: "assistant",
                author: child.agent_name ?? undefined,
                content:
                  child.output ||
                  (child.status === "failed"
                    ? `[${child.agent_name ?? "member"} failed${child.error_message ? `: ${child.error_message}` : ""}]`
                    : `(${child.status})`),
              });
            }
            const finalOut = tree.parent?.output;
            // The parent's final output is usually the last member's output verbatim
            // (e.g. a summarizer is the terminal member) — only append it as its own
            // bubble when it actually differs, so the answer isn't shown twice.
            const lastChildOut = (tree.children ?? []).slice(-1)[0]?.output ?? "";
            if (status === "completed" && finalOut && finalOut.trim() !== lastChildOut.trim()) {
              next.push({ role: "assistant", content: finalOut });
            } else if (status === "failed") {
              next.push({
                role: "assistant",
                content: `[Workflow failed${tree.parent?.error_message ? `: ${tree.parent.error_message}` : ""}]`,
              });
            }
            return next;
          });
          return;
        }
      }
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "[The workflow is still running — check the run's history.]" },
      ]);
    } finally {
      setResuming(false);
    }
  };

  const onDecided = (_runId: string, _decision: "approved" | "denied") => {
    setPendingApproval(null);
    const runId = parkedRunIdRef.current;
    if (runId) void pollResumedResult(runId);
  };

  const send = async () => {
    if (!input.trim() || isStreaming || awaitingApproval || resuming || !id) return;
    const userMsg = input.trim();
    setInput("");
    setPendingApproval(null);
    parkedRunIdRef.current = null;
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setIsStreaming(true);

    try {
      const kc = getKeycloak();
      if (kc?.authenticated) {
        await kc.updateToken(10);
      }
      const token = kc?.token;

      const resp = await fetch(workflowRunStreamUrl(id), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ message: userMsg, session_id: sessionId }),
      });

      if (!resp.ok || !resp.body) {
        let detail = "Error: failed to start workflow run.";
        try {
          const j = await resp.json();
          if (j?.detail) detail = j.detail;
        } catch {
          // non-JSON error body — keep the default message
        }
        setMessages((prev) => [...prev, { role: "assistant", content: detail }]);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const handleFrame = (f: WorkflowStreamFrame) => {
        if (f.type === "agent_start") {
          setMessages((prev) => openAuthorBubble(prev, f.author, mk));
        } else if (f.type === "token") {
          setMessages((prev) => routeToken(prev, f.author, f.content || "", mk));
        } else if (f.type === "tool_call") {
          setMessages((prev) =>
            attachToolCall(prev, f.author, { tool_name: f.tool || "", status: f.status || "ok" }, mk),
          );
        } else if (f.type === "rationale") {
          setMessages((prev) => attachRationale(prev, f.author, f.content || "", mk));
        } else if (f.type === "approval_requested") {
          // Inline self-approve panel — the workflow has parked; deciding resumes it.
          if (f.approval_id) {
            setPendingApproval({
              approval_id: f.approval_id,
              run_id: parkedRunIdRef.current ?? "",
              status: "pending",
              tool: f.tool ?? "",
              args: f.args ?? {},
              risk: f.risk ?? "high",
              reasoning: f.reasoning ?? f.content ?? null,
              requested_by: f.author ?? null,
              requested_by_team: workflow?.team ?? null,
              context: "playground",
              created_at: null,
              decided: false,
            });
          }
        } else if (f.type === "done") {
          if (f.run_id) parkedRunIdRef.current = f.run_id;
        } else if (f.type === "error") {
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              content: `[Error${f.author ? ` · ${f.author}` : ""}: ${f.message || "unknown"}]`,
            },
          ]);
        }
      };

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          for (const line of rawEvent.split("\n")) {
            const trimmed = line.trimStart();
            if (!trimmed.startsWith("data:")) continue;
            const payload = trimmed.slice(5).trim();
            if (!payload) continue;
            try {
              handleFrame(JSON.parse(payload) as WorkflowStreamFrame);
            } catch {
              // malformed frame — skip it
            }
          }
        }
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Error: failed to start workflow run." },
      ]);
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)]">
      {/* Main column */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-slate-200">
          <div className="flex items-center gap-3 min-w-0">
            <Link to={backTo} className="text-slate-400 hover:text-slate-600 shrink-0" title="Back to workflow">
              <ArrowLeft size={18} />
            </Link>
            <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center shrink-0">
              <GitBranch size={15} className="text-purple-600" />
            </div>
            <div className="min-w-0">
              <h1 className="text-sm font-semibold text-slate-900 font-mono truncate">
                {workflow?.name ?? "Workflow"}
              </h1>
              <p className="text-xs text-slate-400">
                {workflow ? `${workflow.execution_shape} · ${workflow.orchestration}` : "Loading…"}
              </p>
            </div>
          </div>
          <button
            onClick={resetConversation}
            className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800"
            title="Start a new conversation"
            data-testid="workflow-chat-new"
          >
            <RefreshCw size={13} /> New conversation
          </button>
        </div>

        {/* Transcript */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3" data-testid="workflow-chat-transcript">
          {messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
              <GitBranch size={28} className="text-slate-300" />
              <p className="text-sm">Send a message to run this workflow.</p>
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[75%] rounded-2xl bg-blue-600 text-white px-4 py-2 text-sm">
                  {m.content}
                </div>
              </div>
            ) : (
              <AttributedBubble
                key={i}
                role="assistant"
                content={m.content}
                author={m.author}
                showLabel={!!m.author}
                streaming={isStreaming && i === messages.length - 1}
                toolCalls={m.toolCalls}
                rationale={m.rationale}
              />
            ),
          )}
          {resuming && (
            <div className="flex items-center gap-2 text-sm text-slate-500" data-testid="workflow-resuming">
              <Loader2 size={14} className="animate-spin" /> Resuming the workflow after approval…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Composer */}
        <div className="border-t border-slate-200 px-6 py-3">
          <div className="flex items-center gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              disabled={isStreaming || awaitingApproval || resuming}
              placeholder={
                awaitingApproval ? "Awaiting approval…" : isStreaming || resuming ? "Running…" : "Message…"
              }
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-400 disabled:bg-slate-50"
              data-testid="workflow-chat-input"
            />
            <button
              onClick={() => void send()}
              disabled={isStreaming || awaitingApproval || resuming || !input.trim()}
              className="btn-primary text-sm py-2 px-3 disabled:opacity-50"
              data-testid="workflow-chat-send"
            >
              {isStreaming ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
        </div>
      </div>

      {/* Inline HITL panel (parity with the single-agent sandbox chat) */}
      {pendingApproval && (
        <ConversationApprovalPanel
          approvals={[pendingApproval]}
          onDecided={onDecided}
          onClose={() => setPendingApproval(null)}
        />
      )}
    </div>
  );
}
