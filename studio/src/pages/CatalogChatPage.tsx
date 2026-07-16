import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, Eye, Info, Loader2, Send, ShieldAlert, ThumbsUp, ThumbsDown } from "lucide-react";
import { getCatalogDetail } from "../api/catalogApi";
import {
  startAgentChat,
  getWorkflowRunTree,
  getCompositeWorkflow,
  getChatApprovalStatus,
  workflowRunStreamUrl,
  type WorkflowRunTree,
  type WorkflowStreamFrame,
} from "../api/registryApi";
import { submitRunFeedback } from "../api/playgroundApi";
import { getKeycloak } from "../lib/keycloak";
import AttributedBubble from "../components/chat/AttributedBubble";
import {
  routeToken,
  openAuthorBubble,
  attachToolCall,
  attachRationale,
  type ToolCall,
} from "../lib/chatStream";

interface Message {
  role: "user" | "assistant";
  content: string;
  // The speaking agent for this bubble. Undefined = single-speaker (unlabeled).
  // A workflow member turn carries the member's agent name here.
  author?: string;
  // POC-2b live rich slots: tool chips + the member's one-line rationale,
  // attached by the stream reducers keyed on `author`.
  toolCalls?: ToolCall[];
  rationale?: string | null;
  // Citation slot (empty in POC-2b; the renderer is ready for POC-4).
  citations?: { source: string; kb: string }[];
  // A completed workflow turn carries its full run tree so each member renders
  // as its own attributed bubble (the whole run no longer collapses to one
  // final-output bubble). Undefined for plain single-agent turns. Used only on
  // the RELOAD/history path — live runs stream member bubbles directly.
  tree?: WorkflowRunTree;
  // Present on a completed assistant turn so the user can rate it. Production
  // feedback is the signal the observability dashboard cares about most.
  runId?: string;
}

// Local copies of the run-tree row helpers (mirrors WorkflowBuilderPage). Kept
// local so this consumer surface stays self-contained; they are pure and tiny.
function statusBadgeCls(status: string): string {
  switch (status) {
    case "running":
      return "bg-blue-100 text-blue-700";
    case "completed":
      return "bg-green-100 text-green-700";
    case "failed":
      return "bg-red-100 text-red-700";
    case "awaiting_approval":
      return "bg-amber-100 text-amber-700";
    case "queued":
    case "pending":
    default:
      return "bg-slate-100 text-slate-600";
  }
}

function fmtLatency(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// A completed workflow turn: each member renders as its own attributed bubble
// (with a compact status/latency/trace step view), followed by the parent's
// final-output summary bubble. Falls back to a single parent bubble when the
// run tree has no children (empty/absent members).
function WorkflowTurn({ tree, showRationale }: { tree: WorkflowRunTree; showRationale: boolean }) {
  const { parent, children } = tree;

  if (children.length === 0) {
    const content =
      parent.output ||
      (parent.error_message
        ? `Workflow failed: ${parent.error_message}`
        : "Workflow completed.");
    return <AttributedBubble role="assistant" content={content} showLabel={false} />;
  }

  return (
    <>
      {children.map((child) => (
        <AttributedBubble
          key={child.id}
          role="assistant"
          author={child.agent_name}
          content={child.output || ""}
          showLabel
          avatar
          toolCalls={child.tool_calls}
          rationale={child.rationale}
          showRationale={showRationale}
        >
          <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-200/70">
            <span
              className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full capitalize ${statusBadgeCls(
                child.status,
              )}`}
            >
              {child.status}
            </span>
            <span className="text-[10px] text-slate-400">{fmtLatency(child.latency_ms)}</span>
            {child.trace_url && (
              <a
                href={child.trace_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[10px] text-blue-600 hover:text-blue-800 font-medium"
              >
                <Eye size={11} /> View Trace
              </a>
            )}
          </div>
        </AttributedBubble>
      ))}
      {parent.output && (
        <AttributedBubble key="__summary" role="assistant" content={parent.output} showLabel={false} />
      )}
      {parent.error_message && (
        <p key="__error" className="text-xs text-red-600 px-1">
          {parent.error_message}
        </p>
      )}
    </>
  );
}

interface PendingApproval {
  approvalId: string;
  toolName: string;
  risk: string;
  runId: string;
}

export default function CatalogChatPage() {
  const { artifactId } = useParams<{ artifactId: string }>();

  const { data } = useQuery({
    queryKey: ["catalog-detail", artifactId],
    queryFn: () => getCatalogDetail(artifactId!),
    enabled: !!artifactId,
  });

  const [searchParams] = useSearchParams();
  // When launched from a specific fleet row, ?dep=<id> pins the run to exactly
  // that deployment. Otherwise fall back to the single running deployment.
  const pinnedDepId = searchParams.get("dep");

  const artifact = data?.artifact;
  const activeDeployment =
    (pinnedDepId && data?.deployments.find((d) => d.id === pinnedDepId)) ||
    data?.deployments.find((d) => d.status === "running");
  const agentName = artifact?.name;
  const isWorkflow = artifact?.type === "workflow";

  // Workflow metadata (member count + orchestration) powers the console shell
  // header/subtitle. Only fetched for workflow artifacts.
  const { data: workflowMeta } = useQuery({
    queryKey: ["composite-workflow", artifact?.source_id],
    queryFn: () => getCompositeWorkflow(artifact!.source_id!),
    enabled: isWorkflow && !!artifact?.source_id,
  });

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  // Console-level toggle that hides/shows every member's amber rationale box
  // (live bubbles + reloaded tree children). Default on, mirrors the mock.
  const [showRationale, setShowRationale] = useState(true);
  // Thumbs feedback per run id (locks the control after a rating is submitted).
  const [feedbackByRun, setFeedbackByRun] = useState<Record<string, 1 | -1>>({});

  const rateRun = async (runId: string, score: 1 | -1) => {
    setFeedbackByRun((prev) => ({ ...prev, [runId]: score }));
    try {
      await submitRunFeedback(runId, score);
    } catch {
      // Roll back so the user can retry if the write failed.
      setFeedbackByRun((prev) => {
        const copy = { ...prev };
        delete copy[runId];
        return copy;
      });
    }
  };
  const [sessionId] = useState(() => crypto.randomUUID());
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // Polls the HITL console for a decision, then auto-resumes — so the consumer
  // never has to click "Check & Resume" (parity with the deployment-chat page).
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Guards the one-time mount reload so it doesn't re-seed on every render.
  const reloadedRef = useRef(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Clean up any poll on unmount.
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const connectResumeStream = useCallback((name: string, runId: string) => {
    setIsStreaming(true);
    setPendingApproval(null);

    const kc = getKeycloak();
    const token = kc?.token;
    const resumeUrl = token
      ? `/api/v1/agents/${name}/chat/${runId}/resume-stream?token=${encodeURIComponent(token)}`
      : `/api/v1/agents/${name}/chat/${runId}/resume-stream`;

    const source = new EventSource(resumeUrl);

    source.onmessage = (event) => {
      const d = JSON.parse(event.data);
      if (d.type === "token") {
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          copy[copy.length - 1] = {
            ...last,
            content: last.content + d.content,
          };
          return copy;
        });
      } else if (d.type === "done") {
        source.close();
        setIsStreaming(false);
      } else if (d.type === "error") {
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          copy[copy.length - 1] = {
            ...last,
            content: last.content + `\n\n[Error: ${d.message}]`,
          };
          return copy;
        });
      }
    };

    source.onerror = () => {
      source.close();
      setIsStreaming(false);
    };
  }, []);

  // Auto-resume: while an approval is pending, poll the HITL console for the
  // decision and reconnect the resume stream as soon as it's decided. Mirrors the
  // deployment-chat page so the consumer doesn't have to click "Check & Resume".
  useEffect(() => {
    if (!pendingApproval || !agentName) return;
    const name = agentName;
    const runId = pendingApproval.runId;
    const id = setInterval(async () => {
      try {
        const s = await getChatApprovalStatus(name, runId);
        if (s.decided) {
          clearInterval(id);
          pollRef.current = null;
          connectResumeStream(name, runId);
        }
      } catch {
        // transient — keep polling
      }
    }, 3000);
    pollRef.current = id;
    return () => {
      clearInterval(id);
      pollRef.current = null;
    };
  }, [pendingApproval, agentName, connectResumeStream]);

  // Poll until the workflow run reaches a terminal state, then hand back the
  // FULL run tree (parent + per-member children) — attribution comes from the
  // children, so we no longer collapse the run to just the parent output.
  // Returns null on timeout (still running).
  const pollWorkflowResult = useCallback(
    async (workflowId: string, runId: string): Promise<WorkflowRunTree | null> => {
      const maxAttempts = 60;
      // The PARENT run flips terminal before the per-member CHILD rows finish
      // recording their outputs, so a naive "return on parent terminal" can capture
      // a child-less tree and collapse the run into one unlabeled bubble (the exact
      // regression this attribution work exists to remove). Once the parent is
      // terminal, return as soon as members appear; if it stays child-less, allow a
      // short settle window before accepting a genuinely member-less run.
      const MAX_SETTLE = 3;
      let settlePolls = 0;
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const tree = await getWorkflowRunTree(workflowId, runId);
          const terminal =
            tree.parent.status === "completed" || tree.parent.status === "failed";
          if (terminal) {
            if (tree.children.length > 0 || settlePolls >= MAX_SETTLE) {
              return tree;
            }
            settlePolls++;
          }
        } catch {
          // keep polling
        }
      }
      return null;
    },
    [],
  );

  // RELOAD/history: on a fresh mount of a workflow console, rehydrate the last
  // completed run's per-member bubbles straight from the backend (run-tree
  // projection carries tool_calls + rationale). This is the save→reload→survives
  // round-trip — the transcript is read from the DB via getWorkflowRunTree, not
  // from the client store. Only seeds when the transcript is empty so it never
  // clobbers a live session.
  useEffect(() => {
    if (!isWorkflow || !artifactId || !artifact?.source_id) return;
    if (reloadedRef.current) return;
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem(`wf-lastrun-${artifactId}`);
    } catch {
      stored = null;
    }
    if (!stored) return;
    reloadedRef.current = true;
    const workflowId = artifact.source_id;
    (async () => {
      const tree = await pollWorkflowResult(workflowId, stored!);
      if (tree && tree.children.length > 0) {
        setMessages((prev) =>
          prev.length === 0
            ? [{ role: "assistant", content: tree.parent.output || "", tree }]
            : prev,
        );
      }
    })();
  }, [isWorkflow, artifactId, artifact?.source_id, pollWorkflowResult]);

  // LIVE workflow console: open the multiplexed SSE stream and drive the SAME
  // author-keyed reducers single-agent chat uses, so each member frame opens/
  // extends its own attributed bubble in real time. EventSource is GET-only and
  // this endpoint needs a POST body, so we consume it via fetch + a
  // ReadableStream reader. The reload/history path (pollWorkflowResult →
  // WorkflowTurn) is unchanged and used only after a reload.
  const sendWorkflowMessage = useCallback(async (userMsg: string) => {
    if (!artifact?.source_id) return;
    const workflowId = artifact.source_id;
    setIsStreaming(true);

    // Each member bubble is opened/extended by the shared reducers, keyed on the
    // frame's `author` (the member's agent name).
    const mk = (author?: string): Message => ({ role: "assistant", content: "", author });

    try {
      const kc = getKeycloak();
      if (kc?.authenticated) {
        await kc.updateToken(10);
      }
      const token = kc?.token;

      const resp = await fetch(workflowRunStreamUrl(workflowId), {
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
      let completedRunId: string | null = null;

      // Parse SSE-framed `data: {json}\n\n` records and drive the reducers.
      const handleFrame = (f: WorkflowStreamFrame) => {
        if (f.type === "done") {
          completedRunId = f.run_id ?? null;
        } else if (f.type === "agent_start") {
          setMessages((prev) => openAuthorBubble(prev, f.author, mk));
        } else if (f.type === "token") {
          setMessages((prev) => routeToken(prev, f.author, f.content || "", mk));
        } else if (f.type === "tool_call") {
          setMessages((prev) =>
            attachToolCall(
              prev,
              f.author,
              { tool_name: f.tool || "", status: f.status || "ok" },
              mk,
            ),
          );
        } else if (f.type === "rationale") {
          setMessages((prev) => attachRationale(prev, f.author, f.content || "", mk));
        } else if (f.type === "error") {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: `[Error${f.author ? ` · ${f.author}` : ""}: ${f.message || "unknown"}]` },
          ]);
        }
        // agent_end → no-op; done captured above; the read loop ends on close.
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

      // Persist the completed run id so a page RELOAD can rehydrate this run's
      // per-member bubbles from the backend (tree projection with tool_calls +
      // rationale) via the reload path below — the save→reload→survives round-trip.
      if (completedRunId && artifactId) {
        try {
          sessionStorage.setItem(`wf-lastrun-${artifactId}`, completedRunId);
        } catch {
          // storage unavailable (private mode) — reload rehydration is best-effort
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
  }, [artifact?.source_id, artifactId, sessionId]);

  const sendAgentMessage = useCallback(async (userMsg: string) => {
    if (!agentName) return;
    setIsStreaming(true);

    try {
      const res = await startAgentChat(agentName, {
        message: userMsg,
        session_id: sessionId,
        context: "production",
        ...(activeDeployment?.id ? { deployment_id: activeDeployment.id } : {}),
      });

      const kc = getKeycloak();
      if (kc?.authenticated) {
        await kc.updateToken(10);
      }
      const freshToken = kc?.token;

      const streamUrl = freshToken
        ? `${res.stream_url}?token=${encodeURIComponent(freshToken)}`
        : res.stream_url;

      const source = new EventSource(streamUrl);
      // Each assistant bubble is opened/extended by the stream reducers, keyed
      // on the frame's `author`. Single-agent is the degenerate one-speaker case
      // (author undefined) — the same code a workflow member turn would use.
      const mk = (author?: string): Message => ({ role: "assistant", content: "", author });

      source.onmessage = (event) => {
        const d = JSON.parse(event.data);
        if (d.type === "agent_start") {
          setMessages((prev) => openAuthorBubble(prev, d.author, mk));
        } else if (d.type === "token") {
          setMessages((prev) => routeToken(prev, d.author, d.content, mk));
        } else if (d.type === "tool_call") {
          // Single-agent chat now surfaces tool chips too (the shared pod-SSE
          // reader stopped dropping tool_call frames — POC-2b 2b-i).
          setMessages((prev) =>
            attachToolCall(prev, d.author, { tool_name: d.tool || d.tool_name || "", status: d.status || "ok" }, mk),
          );
        } else if (d.type === "approval_requested") {
          source.close();
          setIsStreaming(false);
          setPendingApproval({
            approvalId: d.approval_id,
            toolName: d.tool || d.tool_name || "unknown",
            risk: d.risk || d.risk_level || "high",
            runId: res.run_id,
          });
          setMessages((prev) => {
            // Make sure there is an assistant bubble to carry the notice (there
            // may be none yet if approval fires before any token).
            const base = openAuthorBubble(prev, d.author, mk);
            const copy = [...base];
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant") {
              copy[copy.length - 1] = {
                ...last,
                content:
                  last.content ||
                  `Requesting approval to use tool: ${d.tool || d.tool_name || "unknown"}`,
              };
            }
            return copy;
          });
        } else if (d.type === "done") {
          source.close();
          setIsStreaming(false);
          // Tag the completed turn with its run id so the feedback control can
          // rate it. Prefer the id on the done event, fall back to the start id.
          const runId = d.run_id || res.run_id;
          if (runId) {
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              if (last && last.role === "assistant") {
                copy[copy.length - 1] = { ...last, runId };
              }
              return copy;
            });
          }
        }
      };

      source.onerror = () => {
        source.close();
        setIsStreaming(false);
      };
    } catch {
      setIsStreaming(false);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "Error: failed to start chat. Check that this agent has a running production deployment.",
        },
      ]);
    }
  }, [agentName, sessionId, activeDeployment?.id]);

  const sendMessage = async () => {
    if (!input.trim() || isStreaming || !agentName) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);

    if (isWorkflow) {
      await sendWorkflowMessage(userMsg);
    } else {
      await sendAgentMessage(userMsg);
    }
  };

  // Console shell figures: prefer the workflow's declared member count; fall
  // back to the distinct authors seen so far (before metadata loads).
  const memberCount =
    workflowMeta?.member_count ??
    new Set(messages.filter((m) => m.author).map((m) => m.author)).size;
  const orchestration = workflowMeta?.orchestration;

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <div className="border-b border-slate-200 px-6 py-3 flex items-center gap-3 shrink-0">
        <Link
          to={`/catalog/${artifactId}`}
          className="text-slate-400 hover:text-slate-600"
        >
          <ArrowLeft size={16} />
        </Link>
        <Bot size={18} className="text-blue-600 shrink-0" />
        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-semibold text-slate-900 truncate">
            {artifact?.name ?? "Loading..."}
          </h1>
          {artifact?.description && (
            <p className="text-xs text-slate-400 truncate">
              {artifact.description}
            </p>
          )}
        </div>
        <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
          Production
        </span>
        {isWorkflow && (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
            Workflow
          </span>
        )}
        {activeDeployment?.version_label && (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">
            {activeDeployment.version_label}
          </span>
        )}
      </div>

      {/* Workflow console shell (2b-iii): member count + shared-thread subtitle
          + the blue attribution info-bar with the Show-rationale toggle. */}
      {isWorkflow && (
        <div className="border-b border-slate-200 px-6 py-3 shrink-0">
          <h2 className="text-sm font-semibold text-slate-900">
            {(artifact?.name ?? "Workflow")} · {memberCount} agents
          </h2>
          <p className="text-xs text-slate-500 mt-0.5 mb-2">
            {orchestration ? `${orchestration} orchestration · ` : ""}shared conversation
            thread — every agent reads the same transcript.
          </p>
          <div className="flex items-center justify-between rounded-lg bg-blue-50 border border-blue-100 px-3 py-2">
            <p className="text-xs text-blue-800 inline-flex items-center gap-1.5">
              <Info size={13} /> Each turn is attributed to the agent that produced it.
              Rationale is the distilled "why", shared to downstream agents.
            </p>
            <label className="text-xs text-blue-800 inline-flex items-center gap-1.5 cursor-pointer whitespace-nowrap">
              <input
                type="checkbox"
                checked={showRationale}
                onChange={(e) => setShowRationale(e.target.checked)}
                className="accent-blue-600"
              />{" "}
              Show rationale
            </label>
          </div>
        </div>
      )}

      {/* HITL Approval Banner */}
      {pendingApproval && (
        <div data-testid="consumer-approval-banner" className="bg-amber-50 border-b border-amber-200 px-6 py-3 flex items-center gap-3 shrink-0">
          <ShieldAlert size={18} className="text-amber-600 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-amber-800">
              Awaiting approval for tool: <span data-testid="consumer-approval-tool" className="font-mono">{pendingApproval.toolName}</span>
            </p>
            <p className="text-xs text-amber-600 mt-0.5">
              A reviewer must approve this in the{" "}
              <Link to="/hitl" className="underline font-medium">HITL Dashboard</Link>
              . The chat resumes automatically once approved.
            </p>
          </div>
          <span className="flex items-center gap-1.5 text-xs text-amber-600">
            <Loader2 size={14} className="animate-spin" /> Waiting…
          </span>
          <button
            data-testid="consumer-resume-now"
            onClick={() => {
              if (agentName) {
                connectResumeStream(agentName, pendingApproval.runId);
              }
            }}
            className="px-3 py-1.5 bg-amber-600 text-white text-xs font-medium rounded-md hover:bg-amber-700 transition-colors"
            title="The chat resumes automatically; use this only to force a check"
          >
            Resume now
          </button>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && !isStreaming && (
          <div className="flex flex-col items-center justify-center h-full text-center text-slate-400 gap-2">
            <Bot size={32} className="text-slate-300" />
            <p className="text-sm font-medium">Start a conversation</p>
            {artifact?.description && (
              <p className="text-xs max-w-xs">{artifact.description}</p>
            )}
          </div>
        )}
        {messages.map((m, i) => {
          // A completed (reloaded) workflow turn expands into one attributed
          // bubble per member (plus the final-output summary) via its stored
          // run tree, gated on the console's Show-rationale toggle.
          if (m.tree) {
            return <WorkflowTurn key={i} tree={m.tree} showRationale={showRationale} />;
          }
          // Live bubbles carry an `author` for workflow members (labelled +
          // avatar + rich slots) and are unattributed for single-agent chat.
          return (
            <AttributedBubble
              key={i}
              role={m.role}
              content={m.content}
              author={m.author}
              showLabel={!!m.author}
              avatar={!!m.author}
              toolCalls={m.toolCalls}
              rationale={m.rationale}
              showRationale={showRationale}
              streaming={m.role === "assistant" && isStreaming && i === messages.length - 1}
            >
              {m.role === "assistant" && m.runId && (
                <div className="flex items-center gap-1 mt-2 pt-2 border-t border-slate-200">
                  <span className="text-[11px] text-slate-400 mr-1">Helpful?</span>
                  <button
                    onClick={() => rateRun(m.runId!, 1)}
                    disabled={feedbackByRun[m.runId] !== undefined}
                    title="Thumbs up"
                    className={`p-1 rounded hover:bg-emerald-50 disabled:hover:bg-transparent ${
                      feedbackByRun[m.runId] === 1 ? "text-emerald-600" : "text-slate-400"
                    }`}
                  >
                    <ThumbsUp size={13} />
                  </button>
                  <button
                    onClick={() => rateRun(m.runId!, -1)}
                    disabled={feedbackByRun[m.runId] !== undefined}
                    title="Thumbs down"
                    className={`p-1 rounded hover:bg-rose-50 disabled:hover:bg-transparent ${
                      feedbackByRun[m.runId] === -1 ? "text-rose-600" : "text-slate-400"
                    }`}
                  >
                    <ThumbsDown size={13} />
                  </button>
                </div>
              )}
            </AttributedBubble>
          );
        })}
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
            disabled={isStreaming || !agentName}
            placeholder={
              isStreaming
                ? "Waiting for response..."
                : pendingApproval
                  ? "Awaiting tool approval..."
                  : !agentName
                    ? "Loading agent..."
                    : "Message..."
            }
          />
          <button
            type="submit"
            disabled={isStreaming || !input.trim() || !agentName}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1"
          >
            {isStreaming ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Send size={14} />
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
