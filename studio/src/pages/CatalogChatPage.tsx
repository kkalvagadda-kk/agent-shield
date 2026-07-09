import { useState, useRef, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, Loader2, Send } from "lucide-react";
import { getCatalogDetail } from "../api/catalogApi";
import { startAgentChat, triggerWorkflowRun, getWorkflowRunTree } from "../api/registryApi";
import { getKeycloak } from "../lib/keycloak";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function CatalogChatPage() {
  const { artifactId } = useParams<{ artifactId: string }>();

  const { data } = useQuery({
    queryKey: ["catalog-detail", artifactId],
    queryFn: () => getCatalogDetail(artifactId!),
    enabled: !!artifactId,
  });

  const artifact = data?.artifact;
  const activeDeployment = data?.deployments.find(
    (d) => d.status === "running"
  );
  const agentName = artifact?.name;
  const isWorkflow = artifact?.type === "workflow";

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const pollWorkflowResult = useCallback(async (workflowId: string, runId: string) => {
    const maxAttempts = 60;
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const tree = await getWorkflowRunTree(workflowId, runId);
        if (tree.parent.status === "completed") {
          return tree.parent.output || "Workflow completed successfully.";
        }
        if (tree.parent.status === "failed") {
          return `Workflow failed: ${tree.parent.error_message || "unknown error"}`;
        }
      } catch {
        // keep polling
      }
    }
    return "Workflow is still running. Check the Runs tab for results.";
  }, []);

  const sendWorkflowMessage = useCallback(async (userMsg: string) => {
    if (!artifact?.source_id) return;
    setIsStreaming(true);
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      const kc = getKeycloak();
      const userSub = kc?.tokenParsed?.sub || "unknown";

      const result = await triggerWorkflowRun(artifact.source_id, {
        input_payload: { message: userMsg },
        trigger_type: "api",
        run_by: userSub,
      });

      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = { role: "assistant", content: "Running workflow..." };
        return copy;
      });

      const output = await pollWorkflowResult(artifact.source_id, result.run_id);

      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = { role: "assistant", content: output };
        return copy;
      });
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMessages((prev) => {
        const copy = [...prev];
        copy[copy.length - 1] = {
          role: "assistant",
          content: detail || "Error: failed to start workflow run.",
        };
        return copy;
      });
    } finally {
      setIsStreaming(false);
    }
  }, [artifact?.source_id, pollWorkflowResult]);

  const sendAgentMessage = useCallback(async (userMsg: string) => {
    if (!agentName) return;
    setIsStreaming(true);

    try {
      const res = await startAgentChat(agentName, {
        message: userMsg,
        session_id: sessionId,
        context: "production",
      });

      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      const kc = getKeycloak();
      if (kc?.authenticated) {
        await kc.updateToken(10);
      }
      const freshToken = kc?.token;

      const streamUrl = freshToken
        ? `${res.stream_url}?token=${encodeURIComponent(freshToken)}`
        : res.stream_url;

      const source = new EventSource(streamUrl);

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
  }, [agentName, sessionId]);

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
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[70%] px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-blue-600 text-white rounded-br-sm"
                  : "bg-slate-100 text-slate-800 rounded-bl-sm"
              }`}
            >
              {m.content}
              {m.role === "assistant" &&
                isStreaming &&
                i === messages.length - 1 && (
                  <span className="inline-block w-1 h-3 bg-slate-400 ml-0.5 animate-pulse" />
                )}
            </div>
          </div>
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
            disabled={isStreaming || !agentName}
            placeholder={
              isStreaming
                ? "Waiting for response..."
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
