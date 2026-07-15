import { useState } from "react";
import { MessagesSquare, User, Bot, ArrowRight, Database } from "lucide-react";
import { MOCK_CONVERSATIONS, MOCK_MEMORY, type Conversation } from "../../demo/mockData";
import ConsoleContextBar from "./ConsoleContextBar";

export default function ConversationsPage() {
  const [env, setEnv] = useState<"all" | "sandbox" | "production">("all");
  const [selected, setSelected] = useState<Conversation | null>(MOCK_CONVERSATIONS[0]);

  const convos = MOCK_CONVERSATIONS.filter((c) => env === "all" || c.env === env);

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-slate-900 mb-1">Conversations & Memory</h1>
      <p className="text-sm text-slate-500 mb-5">Your past conversations across sandbox and production — reopen one to continue, or inspect what's stored in memory.</p>

      <ConsoleContextBar
        consoles={["Playground (sandbox)", "Deployed agent chat (prod)", "Consumer chat", "Deployment Overview › Memory"]}
        active="Deployed agent chat (prod)"
        note="Not a standalone page — this panel docks into each chat console, scoped to that agent × environment. The admin-facing Memory tab on Deployment Overview already exists today; this makes the same store user-facing and resumable."
      />
      <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-md px-3 py-2 mb-5">
        Shown here as a full page only so it's easy to click through in the preview.
      </p>

      <div className="flex gap-6">
        {/* Conversation list */}
        <div className="w-72 shrink-0">
          <div className="flex gap-1 mb-3">
            {(["all", "production", "sandbox"] as const).map((e) => (
              <button key={e} onClick={() => setEnv(e)} className={`px-2.5 py-1 rounded-md text-xs capitalize transition-colors ${env === e ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>{e}</button>
            ))}
          </div>
          <div className="space-y-1.5">
            {convos.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c)}
                className={`w-full text-left card py-2.5 px-3 transition-all ${selected?.id === c.id ? "border-blue-400 ring-1 ring-blue-100" : "hover:border-slate-300"}`}
              >
                <div className="flex items-center justify-between mb-0.5">
                  <p className="text-sm font-medium text-slate-900 truncate">{c.title}</p>
                  <span className={`badge text-[10px] ${c.env === "production" ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"}`}>{c.env === "production" ? "prod" : "sbx"}</span>
                </div>
                <p className="text-xs text-slate-400 truncate">{c.preview}</p>
                <p className="text-[11px] text-slate-400 mt-1">{c.agent} · {c.turns} turns · {new Date(c.updatedAt).toLocaleDateString()}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Memory viewer */}
        <div className="flex-1 min-w-0">
          {selected && (
            <div className="card">
              <div className="flex items-center justify-between mb-4 pb-3 border-b border-slate-100">
                <div>
                  <p className="font-semibold text-slate-900">{selected.title}</p>
                  <p className="text-xs text-slate-400 inline-flex items-center gap-1.5"><Database size={12} /> {selected.agent} · {selected.env} · stored conversation</p>
                </div>
                <button className="btn-primary text-sm">Continue <ArrowRight size={13} /></button>
              </div>

              <div className="space-y-3">
                {MOCK_MEMORY.map((m, i) => (
                  <div key={i} className={`flex gap-2.5 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
                    <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${m.role === "user" ? "bg-slate-200" : "bg-white border border-slate-200"}`}>
                      {m.role === "user" ? <User size={12} className="text-slate-500" /> : <Bot size={12} className="text-emerald-600" />}
                    </div>
                    <div className={`max-w-md ${m.role === "user" ? "text-right" : ""}`}>
                      <p className="text-[11px] text-slate-400 mb-0.5">{m.author} · {m.at}</p>
                      <div className={`rounded-lg px-3 py-2 text-sm inline-block text-left ${m.role === "user" ? "bg-blue-600 text-white" : "bg-slate-50 border border-slate-200 text-slate-700"}`}>{m.content}</div>
                    </div>
                  </div>
                ))}
              </div>

              <p className="text-xs text-slate-400 mt-4 pt-3 border-t border-slate-100 inline-flex items-center gap-1.5">
                <MessagesSquare size={12} /> This viewer shows the stored thread keyed by session — a real conversation, because the thread is keyed per session (not per turn).
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
