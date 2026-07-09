import { useState, useRef, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, Loader2, Send } from "lucide-react";
import { getAgent, startAgentChat, startDeploymentChat } from "../api/registryApi";
import { getKeycloak } from "../lib/keycloak";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function AgentChatPage() {
  const { name, depId } = useParams<{ name: string; depId?: string }>();

  const { data: agent } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID());
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || isStreaming || !name) return;
    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setIsStreaming(true);

    try {
      const res = depId
        ? await startDeploymentChat(name, depId, { message: userMsg, session_id: sessionId })
        : await startAgentChat(name, { message: userMsg, session_id: sessionId, context: "playground" });

      // Append empty assistant bubble
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      // Get a fresh token from keycloak — the context snapshot may be stale/expired
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
        const data = JSON.parse(event.data);
        if (data.type === "token") {
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, content: last.content + data.content };
            return copy;
          });
        } else if (data.type === "done") {
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
          content: "Error: failed to start chat. Check that this agent is deployed.",
        },
      ]);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-white">
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

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && !isStreaming && (
          <div className="flex flex-col items-center justify-center h-full text-center text-slate-400 gap-2">
            <Bot size={32} className="text-slate-300" />
            <p className="text-sm font-medium">Start a conversation</p>
            {agent?.description && (
              <p className="text-xs max-w-xs">{agent.description}</p>
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
              {m.role === "assistant" && isStreaming && i === messages.length - 1 && (
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
            disabled={isStreaming}
            placeholder={isStreaming ? "Waiting for response…" : "Message…"}
          />
          <button
            type="submit"
            disabled={isStreaming || !input.trim()}
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
