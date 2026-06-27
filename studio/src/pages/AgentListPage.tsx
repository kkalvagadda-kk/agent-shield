import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  ChevronUp,
  ChevronsUpDown,
  Loader2,
  Plus,
  RefreshCw,
  Rocket,
  Search,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { listAgents, type Agent } from "../api/registryApi";
import { cn } from "../lib/utils";

const STATUS: Record<string, { label: string; cls: string }> = {
  active:      { label: "Active",      cls: "bg-green-100 text-green-700" },
  archived:    { label: "Archived",    cls: "bg-slate-100 text-slate-600" },
  deprecated:  { label: "Deprecated", cls: "bg-amber-100 text-amber-700" },
  quarantined: { label: "Quarantined", cls: "bg-red-100 text-red-700" },
};

const col = createColumnHelper<Agent>();

export default function AgentListPage() {
  const navigate = useNavigate();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(200, 0),
    refetchInterval: 15_000,
  });

  const columns = [
    col.accessor("name", {
      header: "Name",
      cell: (info) => (
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
            <Bot size={13} className="text-blue-600" />
          </div>
          <div>
            <p className="font-semibold text-slate-900">{info.getValue()}</p>
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
      cell: (info) => (
        <div className="flex justify-end">
          <button
            onClick={() => navigate(`/agents/${info.row.original.name}/deploy`)}
            className="btn-primary py-1.5 text-xs"
          >
            <Rocket size={12} />
            Deploy
          </button>
        </div>
      ),
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
            Register Agent
          </button>
        </div>
      </div>

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
              <p className="text-slate-400 text-sm mt-1">Register your first agent to get started.</p>
              <button onClick={() => navigate("/agents/new")} className="btn-primary mt-5">
                <Plus size={14} />
                Register Agent
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

function SortIcon({ direction }: { direction: false | "asc" | "desc" }) {
  if (direction === "asc") return <ChevronUp size={12} className="text-blue-600" />;
  if (direction === "desc") return <ChevronDown size={12} className="text-blue-600" />;
  return <ChevronsUpDown size={12} className="text-slate-300" />;
}
