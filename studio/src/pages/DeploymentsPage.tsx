import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { listAllDeployments } from "../api/registryApi";

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  pending:     { label: "Pending",     cls: "bg-amber-100 text-amber-700" },
  deploying:   { label: "Deploying",   cls: "bg-blue-100 text-blue-700"  },
  running:     { label: "Running",     cls: "bg-green-100 text-green-700" },
  failed:      { label: "Failed",      cls: "bg-red-100 text-red-700"    },
  rolled_back: { label: "Rolled back", cls: "bg-slate-100 text-slate-600" },
  terminated:  { label: "Terminated",  cls: "bg-slate-100 text-slate-600" },
};

const FILTER_TABS = [
  { value: "",           label: "All"        },
  { value: "running",    label: "Running"    },
  { value: "deploying",  label: "Deploying"  },
  { value: "failed",     label: "Failed"     },
  { value: "terminated", label: "Terminated" },
] as const;

type FilterValue = typeof FILTER_TABS[number]["value"];

export default function DeploymentsPage() {
  const [statusFilter, setStatusFilter] = useState<FilterValue>("");

  const { data, isLoading } = useQuery({
    queryKey: ["deployments", statusFilter],
    queryFn: () => listAllDeployments(statusFilter || undefined),
    refetchInterval: 30_000,
  });

  const deployments = data?.items ?? [];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Deployments</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          All agent deployments across teams. Auto-refreshes every 30 seconds.
        </p>
      </div>

      {/* Filter tabs */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setStatusFilter(tab.value)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              statusFilter === tab.value
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading…
        </div>
      )}

      {/* Table */}
      {!isLoading && (
        <div className="card p-0 overflow-hidden">
          {deployments.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <p className="text-slate-500 font-medium">No deployments found.</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Agent", "Status", "Deployed At", "Error", "Actions"].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {deployments.map((d) => (
                  <tr
                    key={d.id}
                    className={`hover:bg-slate-50 transition-colors ${
                      d.status === "running" ? "border-l-2 border-l-green-400" : ""
                    }`}
                  >
                    {/* Agent */}
                    <td className="px-4 py-3">
                      {d.agent_name ? (
                        <Link
                          to={`/agents/${d.agent_name}`}
                          className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          {d.agent_name}
                        </Link>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>

                    {/* Status */}
                    <td className="px-4 py-3">
                      {STATUS_LABELS[d.status] ? (
                        <span className={`badge ${STATUS_LABELS[d.status].cls}`}>
                          {STATUS_LABELS[d.status].label}
                        </span>
                      ) : (
                        <span className="badge bg-slate-100 text-slate-600">{d.status}</span>
                      )}
                    </td>

                    {/* Deployed At */}
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      {d.deployed_at
                        ? new Date(d.deployed_at).toLocaleString()
                        : "—"}
                    </td>

                    {/* Error */}
                    <td className="px-4 py-3 text-xs text-red-600 max-w-xs">
                      {d.error_message ? (
                        <span className="line-clamp-2" title={d.error_message}>
                          {d.error_message}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3">
                      {d.status === "running" && d.agent_name && (
                        <Link
                          to={`/agents/${d.agent_name}/chat`}
                          className="text-xs font-medium text-blue-600 hover:text-blue-800"
                        >
                          Chat →
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
