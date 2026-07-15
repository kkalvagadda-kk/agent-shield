import { useState } from "react";
import { Link } from "react-router-dom";
import { Database, Plus, X, FileText } from "lucide-react";
import { MOCK_KBS } from "../../demo/mockData";

export default function KnowledgeBasesPage() {
  const [showNew, setShowNew] = useState(false);

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Knowledge Bases</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Collections of Sources that agents query via <code className="font-mono text-xs bg-slate-100 px-1 rounded">knowledge_search</code>
          </p>
        </div>
        <button onClick={() => setShowNew(true)} className="btn-primary">
          <Plus size={14} /> New Knowledge Base
        </button>
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              {["Name", "Team", "Sources", "Size", "Status", "Updated"].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {MOCK_KBS.map((kb) => {
              const ready = kb.sources.filter((s) => s.status === "ready").length;
              const processing = kb.sources.some((s) => s.status === "processing");
              const failed = kb.sources.some((s) => s.status === "failed");
              const sizeKb = kb.sources.reduce((a, s) => a + s.sizeKb, 0);
              return (
                <tr key={kb.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <Link to={`/knowledge/${kb.id}`} className="flex items-center gap-2 group">
                      <Database size={14} className="text-blue-500 shrink-0" />
                      <div>
                        <p className="font-semibold text-slate-900 group-hover:text-blue-600">{kb.name}</p>
                        <p className="text-xs text-slate-400 truncate max-w-md">{kb.description}</p>
                      </div>
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{kb.team}</td>
                  <td className="px-4 py-3 text-slate-600">
                    <span className="inline-flex items-center gap-1"><FileText size={12} className="text-slate-400" />{kb.sources.length}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{(sizeKb / 1024).toFixed(1)} MB</td>
                  <td className="px-4 py-3">
                    {failed ? (
                      <span className="badge bg-red-100 text-red-700">1 failed</span>
                    ) : processing ? (
                      <span className="badge bg-amber-100 text-amber-700">{ready}/{kb.sources.length} ready</span>
                    ) : (
                      <span className="badge bg-green-100 text-green-700">Ready</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500 text-xs">{new Date(kb.updatedAt).toLocaleDateString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {showNew && <NewKBModal onClose={() => setShowNew(false)} />}
    </div>
  );
}

function NewKBModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="card max-w-md w-full relative" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"><X size={16} /></button>
        <h2 className="text-lg font-semibold text-slate-900 mb-5">New Knowledge Base</h2>
        <div className="space-y-4">
          <div className="space-y-1">
            <label className="label">Name</label>
            <input className="input" placeholder="Company Policies" />
          </div>
          <div className="space-y-1">
            <label className="label">Team</label>
            <select className="input"><option>platform</option><option>support</option><option>risk</option></select>
          </div>
          <div className="space-y-1">
            <label className="label">Description</label>
            <input className="input" placeholder="What this Knowledge Base is for" />
          </div>
          <p className="text-xs text-slate-400">Embedding model uses the platform default in the POC.</p>
        </div>
        <div className="flex justify-end gap-3 pt-4 mt-4 border-t border-slate-100">
          <button onClick={onClose} className="btn-secondary">Cancel</button>
          <button onClick={onClose} className="btn-primary">Create</button>
        </div>
      </div>
    </div>
  );
}
