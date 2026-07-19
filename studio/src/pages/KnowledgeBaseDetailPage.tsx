// ---------------------------------------------------------------------------
// KnowledgeBaseDetailPage.tsx — real KB detail (POC-4).
//
// Lifts the UX-preview detail markup and wires it to the live knowledgeApi:
//   * Sources tab   → uploadSource (real <input type=file>) + listSources with a
//                     refetchInterval WHILE any source is pending|indexing +
//                     chunk drawer via getChunks + reprocessSource/deleteSource.
//   * Retrieval tab → testRetrieval query box → ranked chunks.
//   * Settings tab  → PATCH name/description, delete KB, and the attach-agent
//                     picker (listBoundAgents / bindAgent / unbindAgent).
//
// Status display map (F-6): pending→"Queued", indexing→"Processing",
// ready→"Ready", failed→"Failed".
// ---------------------------------------------------------------------------

import { useMemo, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, UploadCloud, FileText, RotateCw, Trash2, Eye, X, Search,
  Loader2, CheckCircle2, AlertCircle, Bot, Plus,
} from "lucide-react";
import { toast } from "sonner";
import {
  getKB, updateKB, deleteKB,
  uploadSource, listSources, getChunks, reprocessSource, deleteSource,
  testRetrieval, listBoundAgents, bindAgent, unbindAgent,
  type KBSource, type KBSourceStatus, type KBHit,
} from "../api/knowledgeApi";
import { listAgents } from "../api/registryApi";

const STATUS: Record<KBSourceStatus, { cls: string; label: string; spin: boolean; icon: "loader" | "check" | "alert" }> = {
  pending: { cls: "bg-slate-100 text-slate-600", label: "Queued", spin: false, icon: "loader" },
  indexing: { cls: "bg-amber-100 text-amber-700", label: "Processing", spin: true, icon: "loader" },
  ready: { cls: "bg-green-100 text-green-700", label: "Ready", spin: false, icon: "check" },
  failed: { cls: "bg-red-100 text-red-700", label: "Failed", spin: false, icon: "alert" },
};

function StatusBadge({ status }: { status: KBSourceStatus }) {
  const st = STATUS[status];
  const Icon = st.icon === "check" ? CheckCircle2 : st.icon === "alert" ? AlertCircle : Loader2;
  return (
    <span className={`badge inline-flex items-center gap-1 ${st.cls}`}>
      <Icon size={12} className={st.spin ? "animate-spin" : ""} />{st.label}
    </span>
  );
}

type Tab = "sources" | "retrieval" | "settings";

export default function KnowledgeBaseDetailPage() {
  const { id } = useParams();
  const kbId = id ?? "";
  const [tab, setTab] = useState<Tab>("sources");
  const [viewing, setViewing] = useState<KBSource | null>(null);

  const { data: kb } = useQuery({
    queryKey: ["knowledge-base", kbId],
    queryFn: () => getKB(kbId),
    enabled: !!kbId,
  });

  const { data: bound } = useQuery({
    queryKey: ["kb-bound-agents", kbId],
    queryFn: () => listBoundAgents(kbId),
    enabled: !!kbId,
  });

  const { data: sourcesData } = useQuery({
    queryKey: ["kb-sources", kbId],
    queryFn: () => listSources(kbId),
    enabled: !!kbId,
    // Poll WHILE any source is still ingesting (pending|indexing); stop once
    // every source has settled (ready|failed).
    refetchInterval: (query) => {
      const rows = query.state.data;
      return rows && rows.some((s) => s.status === "pending" || s.status === "indexing")
        ? 2500
        : false;
    },
  });

  const sources = useMemo<KBSource[]>(
    () => (Array.isArray(sourcesData) ? sourcesData : []),
    [sourcesData]
  );
  const boundAgents = useMemo(() => (Array.isArray(bound) ? bound : []), [bound]);
  const ready = sources.filter((s) => s.status === "ready").length;

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <Link to="/knowledge" className="text-sm text-slate-500 hover:text-slate-800 inline-flex items-center gap-1 mb-4">
        <ArrowLeft size={14} /> Knowledge Bases
      </Link>

      <div className="flex items-start justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-900">{kb?.name ?? "Knowledge Base"}</h1>
        {kb?.team && <span className="badge bg-slate-100 text-slate-500">{kb.team}</span>}
      </div>
      {kb?.description && <p className="text-sm text-slate-500 mb-4">{kb.description}</p>}
      {boundAgents.length > 0 && (
        <p className="text-xs text-slate-500 mb-5 inline-flex items-center gap-1.5 flex-wrap">
          <Bot size={13} className="text-slate-400" /> Attached to{" "}
          {boundAgents.map((a, i) => (
            <span key={a.agent_id}>
              <code className="font-mono bg-slate-100 px-1 rounded">{a.agent_name}</code>
              {i < boundAgents.length - 1 ? ", " : ""}
            </span>
          ))}
        </p>
      )}

      {/* Tabs */}
      <div className="flex gap-6 border-b border-slate-200 mb-6">
        {([["sources", `Sources · ${ready}/${sources.length} ready`], ["retrieval", "Test retrieval"], ["settings", "Settings"]] as [Tab, string][]).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t ? "border-blue-500 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-800"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "sources" && <SourcesTab kbId={kbId} sources={sources} onView={setViewing} />}
      {tab === "retrieval" && <RetrievalTab kbId={kbId} />}
      {tab === "settings" && <SettingsTab kbId={kbId} />}

      {viewing && <ChunkDrawer kbId={kbId} source={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}

function SourcesTab({ kbId, sources, onView }: { kbId: string; sources: KBSource[]; onView: (s: KBSource) => void }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["kb-sources", kbId] });

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => Promise.all(files.map((f) => uploadSource(kbId, f))),
    onSuccess: () => {
      invalidate();
      toast.success("Uploaded — ingesting…");
    },
    onError: (err) => toast.error((err as Error)?.message ?? "Upload failed."),
  });

  const reprocessMutation = useMutation({
    mutationFn: (sourceId: string) => reprocessSource(kbId, sourceId),
    onSuccess: () => { invalidate(); toast.success("Reprocessing…"); },
    onError: (err) => toast.error((err as Error)?.message ?? "Reprocess failed."),
  });

  const deleteMutation = useMutation({
    mutationFn: (sourceId: string) => deleteSource(kbId, sourceId),
    onSuccess: () => { invalidate(); toast.success("Source deleted."); },
    onError: (err) => toast.error((err as Error)?.message ?? "Delete failed."),
  });

  const onFiles = (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    uploadMutation.mutate(Array.from(fileList));
  };

  return (
    <div>
      {/* Upload zone (real file input) */}
      <label className="card border-2 border-dashed border-slate-300 hover:border-blue-400 hover:bg-blue-50/30 transition-colors flex flex-col items-center py-8 cursor-pointer mb-5 text-center">
        {uploadMutation.isPending ? (
          <Loader2 size={28} className="text-blue-400 mb-2 animate-spin" />
        ) : (
          <UploadCloud size={28} className="text-slate-400 mb-2" />
        )}
        <p className="text-sm font-medium text-slate-700">
          {uploadMutation.isPending ? "Uploading…" : "Drop files or click to upload"}
        </p>
        <p className="text-xs text-slate-400 mt-0.5">PDF, TXT, MD — each Source is ingested (chunked + embedded) after upload</p>
        <input
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.txt,.md,text/plain,text/markdown,application/pdf"
          disabled={uploadMutation.isPending}
          onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }}
        />
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
            {sources.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No sources yet. Upload a file to ingest it.
                </td>
              </tr>
            )}
            {sources.map((s) => {
              const ext = (s.filename.split(".").pop() ?? "").toLowerCase();
              return (
                <tr key={s.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <FileText size={13} className="text-slate-400 shrink-0" />
                      <span className="font-medium text-slate-900">{s.filename}</span>
                    </div>
                    {s.error && <p className="text-xs text-red-500 mt-0.5 pl-5">{s.error}</p>}
                  </td>
                  <td className="px-4 py-3 uppercase text-xs text-slate-500">{ext || "—"}</td>
                  <td className="px-4 py-3 text-slate-600">{(s.size_bytes / 1024).toFixed(1)} KB</td>
                  <td className="px-4 py-3 text-slate-600">{s.status === "ready" ? s.chunk_count : "—"}</td>
                  <td className="px-4 py-3"><StatusBadge status={s.status} /></td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-3">
                      <button onClick={() => onView(s)} disabled={s.status !== "ready"} className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 disabled:opacity-30 transition-colors"><Eye size={12} /> View</button>
                      {(s.status === "failed" || s.status === "ready") && (
                        <button
                          onClick={() => reprocessMutation.mutate(s.id)}
                          disabled={reprocessMutation.isPending}
                          className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 disabled:opacity-40 transition-colors"
                        ><RotateCw size={12} /> Reprocess</button>
                      )}
                      <button
                        onClick={() => deleteMutation.mutate(s.id)}
                        disabled={deleteMutation.isPending}
                        className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 disabled:opacity-40 transition-colors"
                      ><Trash2 size={12} /> Delete</button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-400 mt-3">
        "Ingestion" is a one-time process — not a sync. The status auto-refreshes while a Source is Queued or Processing.
      </p>
    </div>
  );
}

function RetrievalTab({ kbId }: { kbId: string }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<KBHit[] | null>(null);

  const searchMutation = useMutation({
    mutationFn: (query: string) => testRetrieval(kbId, query),
    onSuccess: (res) => setHits(res.hits),
    onError: (err) => toast.error((err as Error)?.message ?? "Search failed."),
  });

  const runSearch = () => {
    if (!q.trim()) return;
    searchMutation.mutate(q.trim());
  };

  return (
    <div>
      <div className="flex gap-2 mb-5">
        <div className="flex-1 relative">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            className="input pl-9"
            placeholder="Type a query to test retrieval…"
          />
        </div>
        <button onClick={runSearch} disabled={searchMutation.isPending || !q.trim()} className="btn-primary">
          {searchMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : "Search"}
        </button>
      </div>
      {hits !== null && (
        <div className="space-y-2">
          {hits.length === 0 ? (
            <p className="text-sm text-slate-400">No matching chunks. Upload Sources and wait for them to be Ready, then try again.</p>
          ) : (
            <>
              <p className="text-xs text-slate-500 mb-2">Top {hits.length} chunks by cosine similarity — this is exactly what the agent would retrieve for this query.</p>
              {hits.map((h) => (
                <div key={h.chunk_id} className="card py-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="inline-flex items-center gap-1.5 text-xs text-slate-500"><FileText size={12} className="text-slate-400" />{h.source_filename}</span>
                    <span className={`badge ${h.score > 0.85 ? "bg-green-100 text-green-700" : h.score > 0.7 ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-500"}`}>{h.score.toFixed(2)}</span>
                  </div>
                  <p className="text-sm text-slate-700">{h.content}</p>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SettingsTab({ kbId }: { kbId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const { data: kb } = useQuery({
    queryKey: ["knowledge-base", kbId],
    queryFn: () => getKB(kbId),
    enabled: !!kbId,
  });

  const [name, setName] = useState<string | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  // Fall back to the loaded KB until the user edits a field.
  const nameVal = name ?? kb?.name ?? "";
  const descVal = description ?? kb?.description ?? "";

  const saveMutation = useMutation({
    mutationFn: () => updateKB(kbId, { name: nameVal.trim(), description: descVal }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-base", kbId] });
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      toast.success("Saved.");
    },
    onError: (err) => toast.error((err as Error)?.message ?? "Save failed."),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteKB(kbId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
      toast.success("Knowledge base deleted.");
      navigate("/knowledge");
    },
    onError: (err) => toast.error((err as Error)?.message ?? "Delete failed."),
  });

  return (
    <div className="max-w-lg space-y-6">
      <div className="space-y-4">
        <div className="space-y-1">
          <label className="label" htmlFor="kb-set-name">Name</label>
          <input id="kb-set-name" className="input" value={nameVal} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label className="label" htmlFor="kb-set-desc">Description</label>
          <input id="kb-set-desc" className="input" value={descVal} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label className="label">Team</label>
          <input className="input bg-slate-50 text-slate-500" value={kb?.team ?? ""} readOnly />
        </div>
        <div className="flex justify-between pt-4 border-t border-slate-100">
          <button
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="inline-flex items-center gap-1 text-sm text-red-600 hover:text-red-800 disabled:opacity-40"
          ><Trash2 size={14} /> Delete Knowledge Base</button>
          <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || !nameVal.trim()} className="btn-primary">
            {saveMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : "Save"}
          </button>
        </div>
      </div>

      <AttachAgentPicker kbId={kbId} />
    </div>
  );
}

// The attach-agent picker: bind an agent so its `knowledge_search` tool is
// scoped to THIS KB (bindAgent also ensures the tool is attached, server-side),
// and unbind to remove that scope.
function AttachAgentPicker({ kbId }: { kbId: string }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState("");

  const { data: bound } = useQuery({
    queryKey: ["kb-bound-agents", kbId],
    queryFn: () => listBoundAgents(kbId),
    enabled: !!kbId,
  });
  const { data: agentsPage } = useQuery({
    queryKey: ["agents", "all"],
    queryFn: () => listAgents(200, 0),
  });

  const boundAgents = useMemo(() => (Array.isArray(bound) ? bound : []), [bound]);
  const boundIds = useMemo(() => new Set(boundAgents.map((b) => b.agent_id)), [boundAgents]);
  const available = useMemo(
    () => (agentsPage?.items ?? []).filter((a) => !boundIds.has(a.id)),
    [agentsPage, boundIds]
  );

  const invalidateBound = () => qc.invalidateQueries({ queryKey: ["kb-bound-agents", kbId] });

  const bindMutation = useMutation({
    mutationFn: (agentId: string) => bindAgent(kbId, agentId),
    onSuccess: () => { invalidateBound(); setSelected(""); toast.success("Agent attached."); },
    onError: (err) => toast.error((err as Error)?.message ?? "Attach failed."),
  });

  const unbindMutation = useMutation({
    mutationFn: (agentId: string) => unbindAgent(kbId, agentId),
    onSuccess: () => { invalidateBound(); toast.success("Agent detached."); },
    onError: (err) => toast.error((err as Error)?.message ?? "Detach failed."),
  });

  return (
    <div className="space-y-3 pt-2">
      <div>
        <label className="label">Attached agents</label>
        <p className="text-xs text-slate-400 mb-2">
          Attaching an agent scopes its <code className="font-mono bg-slate-100 px-1 rounded">knowledge_search</code> tool to this Knowledge Base.
        </p>
      </div>

      {boundAgents.length === 0 ? (
        <p className="text-sm text-slate-400">No agents attached yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {boundAgents.map((a) => (
            <li key={a.agent_id} className="flex items-center justify-between rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
              <span className="inline-flex items-center gap-1.5 text-sm text-slate-700">
                <Bot size={13} className="text-slate-400" /> {a.agent_name}
              </span>
              <button
                onClick={() => unbindMutation.mutate(a.agent_id)}
                disabled={unbindMutation.isPending}
                className="text-xs text-red-600 hover:text-red-800 disabled:opacity-40"
              >Detach</button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex gap-2">
        <select
          className="input flex-1"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          aria-label="Select an agent to attach"
        >
          <option value="">Select an agent to attach…</option>
          {available.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        <button
          onClick={() => selected && bindMutation.mutate(selected)}
          disabled={!selected || bindMutation.isPending}
          className="btn-primary"
        >
          {bindMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <><Plus size={14} /> Attach</>}
        </button>
      </div>
    </div>
  );
}

function ChunkDrawer({ kbId, source, onClose }: { kbId: string; source: KBSource; onClose: () => void }) {
  const { data: chunks, isLoading } = useQuery({
    queryKey: ["kb-chunks", kbId, source.id],
    queryFn: () => getChunks(kbId, source.id),
    enabled: !!kbId,
  });
  const rows = Array.isArray(chunks) ? chunks : [];

  return (
    <div className="fixed inset-0 bg-slate-900/30 z-50 flex justify-end" onClick={onClose}>
      <div className="w-[480px] max-w-full bg-white h-full shadow-xl overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 sticky top-0 bg-white">
          <div>
            <p className="font-semibold text-slate-900 flex items-center gap-2"><FileText size={15} className="text-slate-400" />{source.filename}</p>
            <p className="text-xs text-slate-400">{source.chunk_count} chunks · the retrievable text segments</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3">
          {isLoading && (
            <p className="text-sm text-slate-400 inline-flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Loading chunks…</p>
          )}
          {!isLoading && rows.length === 0 && (
            <p className="text-sm text-slate-400">No chunks for this source.</p>
          )}
          {rows.map((c) => (
            <div key={c.id} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-mono text-slate-400">chunk #{c.chunk_index}</span>
              </div>
              <p className="text-sm text-slate-700">{c.content}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
