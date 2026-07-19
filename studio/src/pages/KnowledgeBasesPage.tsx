// ---------------------------------------------------------------------------
// KnowledgeBasesPage.tsx — real Knowledge Base list (POC-4).
//
// Lifts the UX-preview markup (pages/preview/KnowledgeBasesPage.tsx) but swaps
// the local MOCK_KBS for a live `useQuery(listKBs)` and wires the New-KB modal
// to `createKB` + query invalidation, so a created KB appears after the refetch
// (the save→reload round-trip). `team` / `created_by` are set server-side.
// ---------------------------------------------------------------------------

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Database, Plus, X, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { listKBs, createKB, type KnowledgeBase } from "../api/knowledgeApi";

const KB_QUERY_KEY = ["knowledge-bases"];

export default function KnowledgeBasesPage() {
  const [showNew, setShowNew] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: KB_QUERY_KEY,
    queryFn: () => listKBs(),
  });

  // Normalize at the boundary: the real API returns an array; the DEMO mock
  // adapter returns an empty paginated shape for unknown routes. Guard both.
  const kbs = useMemo<KnowledgeBase[]>(
    () => (Array.isArray(data) ? data : []),
    [data]
  );

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Knowledge Bases</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Collections of Sources that agents query via{" "}
            <code className="font-mono text-xs bg-slate-100 px-1 rounded">knowledge_search</code>
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
              {["Name", "Team", "Sources", "Status", "Updated"].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-slate-400">
                  <Loader2 size={16} className="inline animate-spin mr-2" /> Loading knowledge bases…
                </td>
              </tr>
            )}
            {isError && !isLoading && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-red-500">
                  Failed to load knowledge bases.
                </td>
              </tr>
            )}
            {!isLoading && !isError && kbs.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-slate-400">
                  No knowledge bases yet. Create one to start uploading Sources.
                </td>
              </tr>
            )}
            {kbs.map((kb) => {
              const ready = kb.ready_count;
              const total = kb.source_count;
              return (
                <tr key={kb.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <Link to={`/knowledge/${kb.id}`} className="flex items-center gap-2 group">
                      <Database size={14} className="text-blue-500 shrink-0" />
                      <div>
                        <p className="font-semibold text-slate-900 group-hover:text-blue-600">{kb.name}</p>
                        {kb.description && (
                          <p className="text-xs text-slate-400 truncate max-w-md">{kb.description}</p>
                        )}
                      </div>
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{kb.team}</td>
                  <td className="px-4 py-3 text-slate-600">
                    <span className="inline-flex items-center gap-1"><FileText size={12} className="text-slate-400" />{total}</span>
                  </td>
                  <td className="px-4 py-3">
                    {total === 0 ? (
                      <span className="badge bg-slate-100 text-slate-500">Empty</span>
                    ) : ready === total ? (
                      <span className="badge bg-green-100 text-green-700">Ready</span>
                    ) : (
                      <span className="badge bg-amber-100 text-amber-700">{ready}/{total} ready</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500 text-xs">{new Date(kb.updated_at).toLocaleDateString()}</td>
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
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      createKB({ name: name.trim(), ...(description.trim() ? { description: description.trim() } : {}) }),
    onSuccess: () => {
      // Refetch the list so the new KB shows up (save → reload).
      qc.invalidateQueries({ queryKey: KB_QUERY_KEY });
      toast.success("Knowledge base created.");
      onClose();
    },
    onError: (err) => {
      toast.error((err as Error)?.message ?? "Failed to create knowledge base.");
    },
  });

  const canSubmit = name.trim().length > 0 && !createMutation.isPending;

  return (
    <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="card max-w-md w-full relative" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"><X size={16} /></button>
        <h2 className="text-lg font-semibold text-slate-900 mb-5">New Knowledge Base</h2>
        <div className="space-y-4">
          <div className="space-y-1">
            <label className="label" htmlFor="kb-name">Name</label>
            <input
              id="kb-name"
              className="input"
              placeholder="Company Policies"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-1">
            <label className="label" htmlFor="kb-desc">Description</label>
            <input
              id="kb-desc"
              className="input"
              placeholder="What this Knowledge Base is for"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <p className="text-xs text-slate-400">
            The KB is created under your team. Embedding uses the platform default in the POC.
          </p>
        </div>
        <div className="flex justify-end gap-3 pt-4 mt-4 border-t border-slate-100">
          <button onClick={onClose} className="btn-secondary" disabled={createMutation.isPending}>Cancel</button>
          <button
            onClick={() => createMutation.mutate()}
            className="btn-primary"
            disabled={!canSubmit}
          >
            {createMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
