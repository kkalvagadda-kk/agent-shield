import { useState } from "react";
import { Bot, User, Lightbulb, Database, Info } from "lucide-react";
import { MOCK_THREAD, type AgentTurn } from "../../demo/mockData";
import ConsoleContextBar from "./ConsoleContextBar";

export default function MultiAgentChatPage() {
  const [showRationale, setShowRationale] = useState(true);

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-900">Refund Assistant</h1>
        <span className="badge bg-indigo-100 text-indigo-700">Workflow · 3 agents</span>
      </div>
      <p className="text-sm text-slate-500 mb-4">
        Supervisor orchestration · shared conversation thread — every agent reads the same transcript.
      </p>

      <ConsoleContextBar
        consoles={["Playground", "Consumer chat", "Workflow Builder run panel", "Eval Results"]}
        active="Consumer chat"
        note="Not a standalone page — this is the shared-transcript renderer, reused by every console that shows a workflow conversation. Today only the Workflow Builder shows per-agent output (as cards); consumer chat collapses a whole run into one bubble."
      />

      <div className="flex items-center justify-between rounded-lg bg-blue-50 border border-blue-100 px-3 py-2 mb-5">
        <p className="text-xs text-blue-800 inline-flex items-center gap-1.5"><Info size={13} /> Each turn is attributed to the agent that produced it. Rationale is the distilled "why", shared to downstream agents.</p>
        <label className="text-xs text-blue-800 inline-flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={showRationale} onChange={(e) => setShowRationale(e.target.checked)} className="accent-blue-600" /> Show rationale
        </label>
      </div>

      <div className="space-y-4">
        {MOCK_THREAD.map((t, i) => (
          <Turn key={i} turn={t} showRationale={showRationale} />
        ))}
      </div>

      <div className="mt-6 flex gap-2">
        <input className="input flex-1" placeholder="Reply…" />
        <button className="btn-primary">Send</button>
      </div>
    </div>
  );
}

function Turn({ turn, showRationale }: { turn: AgentTurn; showRationale: boolean }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${isUser ? "bg-slate-200" : "bg-white border border-slate-200"}`}>
        {isUser ? <User size={15} className="text-slate-500" /> : <Bot size={15} className={turn.color} />}
      </div>
      <div className={`flex-1 min-w-0 ${isUser ? "flex flex-col items-end" : ""}`}>
        <p className={`text-xs font-semibold mb-1 ${turn.color}`}>{turn.author}</p>

        {!isUser && turn.tool && (
          <div className="inline-flex items-center gap-1.5 text-xs text-slate-500 bg-slate-100 rounded-md px-2 py-1 mb-1.5">
            <Database size={11} /> called <code className="font-mono">{turn.tool}</code>
          </div>
        )}

        {!isUser && showRationale && turn.rationale && (
          <div className="flex items-start gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-md px-2.5 py-1.5 mb-1.5 max-w-lg">
            <Lightbulb size={12} className="mt-0.5 shrink-0" />
            <span><span className="font-semibold">Rationale:</span> {turn.rationale}</span>
          </div>
        )}

        <div className={`rounded-lg px-3.5 py-2.5 text-sm max-w-lg ${isUser ? "bg-blue-600 text-white" : "bg-white border border-slate-200 text-slate-700"}`}>
          {turn.content}
        </div>

        {turn.citations && turn.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-1.5">
            {turn.citations.map((c, i) => (
              <span key={i} className="inline-flex items-center gap-1 text-xs text-slate-600 bg-slate-100 rounded-md px-2 py-0.5">
                <Database size={10} className="text-blue-500" /> {c.source} <span className="text-slate-400">· {c.kb}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
