import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  deleteAgent,
  getAgentHealth,
  listAgents,
  listProviders,
  listTools,
  updateAgent,
  type Agent,
} from "../api/registryApi";
import DeployModal from "../components/DeployModal";

const STATUS: Record<string, { label: string; cls: string }> = {
  active:      { label: "Active",      cls: "bg-green-100 text-green-700" },
  archived:    { label: "Archived",    cls: "bg-slate-100 text-slate-600" },
  deprecated:  { label: "Deprecated", cls: "bg-amber-100 text-amber-700" },
  quarantined: { label: "Quarantined", cls: "bg-red-100 text-red-700" },
};

const HEALTH_DOT: Record<string, string> = {
  healthy: "bg-green-500",
  degraded: "bg-amber-500",
  failing: "bg-red-500",
};

// Per-agent health dot. Fetches /agents/{name}/health independently and
// renders a green/yellow/red status dot; falls back to a neutral dot on error.
function HealthDot({ name }: { name: string }) {
  const { data, isError } = useQuery({
    queryKey: ["agent-health", name],
    queryFn: () => getAgentHealth(name),
    refetchInterval: 30_000,
    retry: false,
  });
  const cls = isError || !data ? "bg-slate-300" : HEALTH_DOT[data.health] ?? "bg-slate-300";
  const title = data ? `${data.mode} · ${data.health}` : "health unknown";
  return (
    <span
      title={title}
      className={`inline-block w-2 h-2 rounded-full shrink-0 ${cls}`}
    />
  );
}

const col = createColumnHelper<Agent>();

export default function AgentListPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [deployTarget, setDeployTarget] = useState<string | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(200, 0, "active"),
    refetchInterval: 15_000,
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => deleteAgent(name),
    onSuccess: () => {
      toast.success("Agent deleted.");
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to delete agent.");
    },
  });

  const columns = [
    col.accessor("name", {
      header: "Name",
      cell: (info) => (
        <div className="flex items-center gap-2">
          <HealthDot name={info.getValue()} />
          <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
            <Bot size={13} className="text-blue-600" />
          </div>
          <div>
            <Link
              to={`/agents/${info.getValue()}`}
              className="font-semibold text-blue-600 hover:text-blue-800 hover:underline"
            >
              {info.getValue()}
            </Link>
            {info.row.original.description && (
              <p className="text-xs text-slate-400 truncate max-w-xs">
                {info.row.original.description}
              </p>
            )}
          </div>
        </div>
      ),
    }),
    col.accessor("team", {
      header: "Team",
      cell: (info) => <span className="text-slate-600">{info.getValue()}</span>,
    }),
    col.accessor("agent_type", {
      header: "Type",
      cell: (info) => (
        <span className="badge bg-slate-100 text-slate-600">{info.getValue()}</span>
      ),
    }),
    col.accessor("latest_version_number", {
      header: "Version",
      cell: (info) => (
        <span className="font-mono text-xs text-slate-500">
          {info.getValue() != null ? `v${info.getValue()}` : "—"}
        </span>
      ),
    }),
    col.accessor("status", {
      header: "Status",
      cell: (info) => {
        const s = STATUS[info.getValue()] ?? { label: info.getValue(), cls: "bg-slate-100 text-slate-600" };
        return <span className={`badge ${s.cls}`}>{s.label}</span>;
      },
    }),
    col.accessor("updated_at", {
      header: "Updated",
      cell: (info) => (
        <span className="text-slate-400 text-xs">
          {new Date(info.getValue()).toLocaleDateString()}
        </span>
      ),
    }),
    col.display({
      id: "actions",
      header: "",
      cell: (info) => {
        const agent = info.row.original;
        return (
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={() => setDeployTarget(agent.name)}
              className="btn-primary py-1.5 text-xs"
            >
              <Rocket size={12} />
              Deploy
            </button>
            <button
              onClick={() => {
                setEditingAgent(agent);
              }}
              className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 transition-colors"
            >
              <Pencil size={12} />
              Edit
            </button>
            <button
              onClick={() => {
                if (confirm(`Delete agent "${agent.name}"? This soft-deletes it (status → deprecated).`)) {
                  deleteMutation.mutate(agent.name);
                }
              }}
              disabled={
                deleteMutation.isPending &&
                deleteMutation.variables === agent.name
              }
              className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 disabled:opacity-50 transition-colors"
            >
              {deleteMutation.isPending && deleteMutation.variables === agent.name ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Trash2 size={12} />
              )}
              Delete
            </button>
          </div>
        );
      },
    }),
  ];

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Agents</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Manage and deploy your AI agents
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
          <button onClick={() => navigate("/agents/new")} className="btn-primary">
            <Plus size={14} />
            Create Agent
          </button>
        </div>
      </div>

      {/* Inline edit form */}
      {editingAgent && (
        <AgentEditForm
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
          onSaved={() => {
            setEditingAgent(null);
            qc.invalidateQueries({ queryKey: ["agents"] });
          }}
        />
      )}

      {deployTarget && (
        <DeployModal
          agentName={deployTarget}
          onClose={() => setDeployTarget(null)}
          onDeployed={() => qc.invalidateQueries({ queryKey: ["agents"] })}
        />
      )}

      {/* Search + stats row */}
      <div className="flex items-center justify-between mb-3 gap-3">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            className="input pl-8 w-64 text-sm"
            placeholder="Search agents…"
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
          />
        </div>
        {data && (
          <p className="text-xs text-slate-400 shrink-0">
            {table.getFilteredRowModel().rows.length} of {data.total} agent
            {data.total !== 1 ? "s" : ""}
          </p>
        )}
      </div>

      {/* States */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading agents…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load agents: {String(error)}
        </div>
      )}

      {data && (
        <div className="card p-0 overflow-hidden">
          {data.items.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <Bot size={40} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">No agents yet</p>
              <p className="text-slate-400 text-sm mt-1">Create your first agent to get started.</p>
              <button onClick={() => navigate("/agents/new")} className="btn-primary mt-5">
                <Plus size={14} />
                Create Agent
              </button>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id} className="border-b border-slate-100 bg-slate-50">
                    {hg.headers.map((header) => (
                      <th
                        key={header.id}
                        className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                      >
                        {header.column.getCanSort() ? (
                          <button
                            onClick={header.column.getToggleSortingHandler()}
                            className="inline-flex items-center gap-1 hover:text-slate-900 transition-colors"
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            <SortIcon direction={header.column.getIsSorted()} />
                          </button>
                        ) : (
                          flexRender(header.column.columnDef.header, header.getContext())
                        )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="divide-y divide-slate-100">
                {table.getRowModel().rows.length === 0 ? (
                  <tr>
                    <td colSpan={columns.length} className="px-4 py-12 text-center text-slate-400 text-sm">
                      No agents match your search.
                    </td>
                  </tr>
                ) : (
                  table.getRowModel().rows.map((row) => (
                    <tr key={row.id} className="hover:bg-slate-50 transition-colors">
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-3">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline edit form
// ---------------------------------------------------------------------------
function AgentEditForm({
  agent,
  onClose,
  onSaved,
}: {
  agent: Agent;
  onClose: () => void;
  onSaved: () => void;
}) {
  const meta = (agent as Agent & { metadata?: Record<string, unknown> }).metadata ?? {};
  const [description, setDescription] = useState(agent.description ?? "");
  const [agentStatus, setAgentStatus] = useState(agent.status);
  const [instructions, setInstructions] = useState((meta.instructions as string) ?? "");
  const [selectedProvider, setSelectedProvider] = useState((meta.llm_provider_id as string) ?? "");
  const [selectedTools, setSelectedTools] = useState<string[]>((meta.tools as string[]) ?? []);

  const { data: providers } = useQuery({
    queryKey: ["providers"],
    queryFn: () => listProviders(),
  });
  const { data: tools } = useQuery({
    queryKey: ["tools"],
    queryFn: () => listTools(200),
  });

  const toggleTool = (toolName: string) => {
    setSelectedTools((prev) =>
      prev.includes(toolName) ? prev.filter((t) => t !== toolName) : [...prev, toolName]
    );
  };

  const mutation = useMutation({
    mutationFn: () => {
      const newMeta = { ...meta, instructions, tools: selectedTools, llm_provider_id: selectedProvider || undefined };
      return updateAgent(agent.name, {
        description: description || undefined,
        status: agentStatus,
        metadata: newMeta,
      });
    },
    onSuccess: () => {
      toast.success("Agent updated.");
      onSaved();
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to update agent.");
    },
  });

  return (
    <div className="card mb-6 relative">
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"
      >
        <X size={16} />
      </button>
      <h2 className="text-lg font-semibold text-slate-900 mb-5">
        Edit Agent — {agent.name}
      </h2>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
        className="space-y-4"
      >
        <div className="grid grid-cols-2 gap-4">
          {/* Name — read-only */}
          <div className="space-y-1">
            <label className="label">Name</label>
            <input
              className="input bg-slate-50 text-slate-500 cursor-not-allowed font-mono"
              value={agent.name}
              disabled
            />
          </div>
          {/* Team — read-only */}
          <div className="space-y-1">
            <label className="label">Team</label>
            <input
              className="input bg-slate-50 text-slate-500 cursor-not-allowed"
              value={agent.team}
              disabled
            />
          </div>
        </div>

        {/* Description */}
        <div className="space-y-1">
          <label className="label">Description</label>
          <textarea
            className="input resize-y"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What does this agent do?"
          />
        </div>

        {/* Instructions */}
        <div className="space-y-1">
          <label className="label">Instructions (System Prompt)</label>
          <textarea
            className="input resize-y font-mono text-sm"
            rows={6}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="You are a helpful agent that..."
          />
        </div>

        {/* Model (LLM Provider) */}
        <div className="space-y-1">
          <label className="label">Model (LLM Provider)</label>
          <select
            className="input"
            value={selectedProvider}
            onChange={(e) => setSelectedProvider(e.target.value)}
          >
            <option value="">— None —</option>
            {providers?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.default_model})
              </option>
            ))}
          </select>
        </div>

        {/* Tools */}
        <div className="space-y-1">
          <label className="label">Tools</label>
          <div className="border border-slate-200 rounded p-3 max-h-40 overflow-y-auto space-y-1">
            {tools?.items.length === 0 && (
              <p className="text-xs text-slate-400">No tools registered.</p>
            )}
            {tools?.items.map((t) => (
              <label key={t.name} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-slate-50 px-1 py-0.5 rounded">
                <input
                  type="checkbox"
                  checked={selectedTools.includes(t.name)}
                  onChange={() => toggleTool(t.name)}
                  className="rounded"
                />
                <span className="font-mono text-xs">{t.name}</span>
                {t.description && <span className="text-xs text-slate-400 truncate">— {t.description}</span>}
              </label>
            ))}
          </div>
        </div>

        {/* Status */}
        <div className="space-y-1">
          <label className="label">Status</label>
          <select
            className="input"
            value={agentStatus}
            onChange={(e) => setAgentStatus(e.target.value)}
          >
            <option value="active">Active</option>
            <option value="archived">Archived</option>
            <option value="deprecated">Deprecated</option>
          </select>
        </div>

        <div className="flex justify-end gap-3 pt-2 border-t border-slate-100">
          <button type="button" onClick={onClose} className="btn-secondary">
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="btn-primary"
          >
            {mutation.isPending ? (
              <>
                <Loader2 size={14} className="animate-spin" /> Saving…
              </>
            ) : (
              "Save Changes"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

function SortIcon({ direction }: { direction: false | "asc" | "desc" }) {
  if (direction === "asc") return <ChevronUp size={12} className="text-blue-600" />;
  if (direction === "desc") return <ChevronDown size={12} className="text-blue-600" />;
  return <ChevronsUpDown size={12} className="text-slate-300" />;
}
