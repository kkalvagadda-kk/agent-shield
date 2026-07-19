import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft, UploadCloud, FileText, RotateCw, Trash2, Eye, X, Search,
  Loader2, CheckCircle2, AlertCircle, Bot,
} from "lucide-react";
import { MOCK_KBS, MOCK_CHUNKS, MOCK_RETRIEVAL, type IngestStatus, type KBSource } from "../../demo/mockData";

const STATUS: Record<IngestStatus, { cls: string; label: string; icon: React.ReactNode }> = {
  queued: { cls: "bg-slate-100 text-slate-600", label: "Queued", icon: <Loader2 size={12} /> },
  processing: { cls: "bg-amber-100 text-amber-700", label: "Processing", icon: <Loader2 size={12} className="animate-spin" /> },
  ready: { cls: "bg-green-100 text-green-700", label: "Ready", icon: <CheckCircle2 size={12} /> },
  failed: { cls: "bg-red-100 text-red-700", label: "Failed", icon: <AlertCircle size={12} /> },
};

type Tab = "sources" | "retrieval" | "settings";

export default function KnowledgeBaseDetailPage() {
  const { id } = useParams();
  const kb = MOCK_KBS.find((k) => k.id === id) ?? MOCK_KBS[0];
  const [tab, setTab] = useState<Tab>("sources");
  const [viewing, setViewing] = useState<KBSource | null>(null);

  const ready = kb.sources.filter((s) => s.status === "ready").length;

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <Link to="/knowledge" className="text-sm text-slate-500 hover:text-slate-800 inline-flex items-center gap-1 mb-4">
        <ArrowLeft size={14} /> Knowledge Bases
      </Link>

      <div className="flex items-start justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-900">{kb.name}</h1>
        <span className="badge bg-slate-100 text-slate-500">{kb.team}</span>
      </div>
      <p className="text-sm text-slate-500 mb-4">{kb.description}</p>
      {kb.attachedAgents.length > 0 && (
        <p className="text-xs text-slate-500 mb-5 inline-flex items-center gap-1.5">
          <Bot size={13} className="text-slate-400" /> Attached to{" "}
          {kb.attachedAgents.map((a, i) => (
            <span key={a}><code className="font-mono bg-slate-100 px-1 rounded">{a}</code>{i < kb.attachedAgents.length - 1 ? ", " : ""}</span>
          ))}
        </p>
      )}

      {/* Tabs */}
      <div className="flex gap-6 border-b border-slate-200 mb-6">
        {([["sources", `Sources · ${ready}/${kb.sources.length} ready`], ["retrieval", "Test retrieval"], ["settings", "Settings"]] as [Tab, string][]).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t ? "border-blue-500 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-800"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "sources" && <SourcesTab kb={kb} onView={setViewing} />}
      {tab === "retrieval" && <RetrievalTab />}
      {tab === "settings" && <SettingsTab kb={kb} />}

      {viewing && <ChunkDrawer source={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

function SourcesTab({ kb, onView }: { kb: typeof MOCK_KBS[0]; onView: (s: KBSource) => void }) {
  return (
    <div>
      {/* Upload zone */}
      <label className="card border-2 border-dashed border-slate-300 hover:border-blue-400 hover:bg-blue-50/30 transition-colors flex flex-col items-center py-8 cursor-pointer mb-5 text-center">
        <UploadCloud size={28} className="text-slate-400 mb-2" />
        <p className="text-sm font-medium text-slate-700">Drop files or click to upload</p>
        <p className="text-xs text-slate-400 mt-0.5">PDF, TXT, MD, DOCX — each Source is ingested (chunked + embedded) after upload</p>
        <input type="file" multiple className="hidden" />
      </label>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              {["Source", "Type", "Size", "Chunks", "Status", ""].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {kb.sources.map((s) => {
              const st = STATUS[s.status];
              return (
                <tr key={s.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <FileText size={13} className="text-slate-400 shrink-0" />
                      <span className="font-medium text-slate-900">{s.name}</span>
                    </div>
                    {s.error && <p className="text-xs text-red-500 mt-0.5 pl-5">{s.error}</p>}
                  </td>
                  <td className="px-4 py-3 uppercase text-xs text-slate-500">{s.type}</td>
                  <td className="px-4 py-3 text-slate-600">{s.sizeKb} KB</td>
                  <td className="px-4 py-3 text-slate-600">{s.status === "ready" ? s.chunks : "—"}</td>
                  <td className="px-4 py-3">
                    <span className={`badge inline-flex items-center gap-1 ${st.cls}`}>{st.icon}{st.label}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-3">
                      <button onClick={() => onView(s)} disabled={s.status !== "ready"} className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 disabled:opacity-30 transition-colors"><Eye size={12} /> View</button>
                      {(s.status === "failed" || s.status === "ready") && (
                        <button className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 transition-colors"><RotateCw size={12} /> Reprocess</button>
                      )}
                      <button className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 transition-colors"><Trash2 size={12} /> Delete</button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-400 mt-3">
        "Ingestion" is a one-time process — not a sync. Live-source sync (last-synced / re-sync) is a connector concept, deferred to post-MVP.
      </p>
    </div>
  );
}

function RetrievalTab() {
  const [q, setQ] = useState("When do refunds need approval?");
  const [ran, setRan] = useState(true);
  return (
    <div>
      <div className="flex gap-2 mb-5">
        <div className="flex-1 relative">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} className="input pl-9" placeholder="Type a query to test retrieval…" />
        </div>
        <button onClick={() => setRan(true)} className="btn-primary">Search</button>
      </div>
      {ran && (
        <div className="space-y-2">
          <p className="text-xs text-slate-500 mb-2">Top {MOCK_RETRIEVAL.length} chunks by cosine similarity — this is exactly what the agent would retrieve for this query.</p>
          {MOCK_RETRIEVAL.map((h, i) => (
            <div key={i} className="card py-3">
              <div className="flex items-center justify-between mb-1.5">
                <span className="inline-flex items-center gap-1.5 text-xs text-slate-500"><FileText size={12} className="text-slate-400" />{h.source}</span>
                <span className={`badge ${h.score > 0.85 ? "bg-green-100 text-green-700" : h.score > 0.7 ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500"}`}>{h.score.toFixed(2)}</span>
              </div>
              <p className="text-sm text-slate-700">{h.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SettingsTab({ kb }: { kb: typeof MOCK_KBS[0] }) {
  return (
    <div className="max-w-lg space-y-4">
      <div className="space-y-1"><label className="label">Name</label><input className="input" defaultValue={kb.name} /></div>
      <div className="space-y-1"><label className="label">Description</label><input className="input" defaultValue={kb.description} /></div>
      <div className="space-y-1"><label className="label">Team</label><input className="input bg-slate-50 text-slate-500" defaultValue={kb.team} readOnly /></div>
      <div className="space-y-1"><label className="label">Embedding model</label><input className="input bg-slate-50 text-slate-500" defaultValue={kb.embeddingModel} readOnly /><p className="text-xs text-slate-400">Read-only in the POC — a bank-wide change would trigger a full re-index.</p></div>
      <div className="flex justify-between pt-4 border-t border-slate-100">
        <button className="inline-flex items-center gap-1 text-sm text-red-600 hover:text-red-800"><Trash2 size={14} /> Delete Knowledge Base</button>
        <button className="btn-primary">Save</button>
      </div>
    </div>
  );
}

function ChunkDrawer({ source, onClose }: { source: KBSource; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-slate-900/30 z-50 flex justify-end" onClick={onClose}>
      <div className="w-[480px] max-w-full bg-white h-full shadow-xl overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 sticky top-0 bg-white">
          <div>
            <p className="font-semibold text-slate-900 flex items-center gap-2"><FileText size={15} className="text-slate-400" />{source.name}</p>
            <p className="text-xs text-slate-400">{source.chunks} chunks · the retrievable text segments</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3">
          {MOCK_CHUNKS.map((c) => (
            <div key={c.index} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-mono text-slate-400">chunk #{c.index}</span>
                <span className="text-xs text-slate-400">{c.tokens} tokens</span>
              </div>
              <p className="text-sm text-slate-700">{c.text}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
