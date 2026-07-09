import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2, MessageCircle, Pause, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { listFleetDeployments, updateDeployment } from "../api/catalogApi";

const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
  pending:      { label: "Pending",      cls: "bg-amber-100 text-amber-700" },
  deploying:    { label: "Deploying",    cls: "bg-blue-100 text-blue-700"  },
  running:      { label: "Running",      cls: "bg-green-100 text-green-700" },
  suspending:   { label: "Suspending",   cls: "bg-yellow-100 text-yellow-700" },
  suspended:    { label: "Suspended",    cls: "bg-slate-100 text-slate-600" },
  terminating:  { label: "Terminating",  cls: "bg-orange-100 text-orange-700" },
  failed:       { label: "Failed",       cls: "bg-red-100 text-red-700"    },
  terminated:   { label: "Terminated",   cls: "bg-slate-100 text-slate-600" },
};

const FILTER_TABS = [
  { value: "",            label: "All"        },
  { value: "running",     label: "Running"    },
  { value: "deploying",   label: "Deploying"  },
  { value: "suspended",   label: "Suspended"  },
  { value: "failed",      label: "Failed"     },
  { value: "terminated",  label: "Terminated" },
] as const;

type FilterValue = typeof FILTER_TABS[number]["value"];

export default function DeploymentsPage() {
  const [statusFilter, setStatusFilter] = useState<FilterValue>("");
  const queryClient = useQueryClient();

  const { data: deployments = [], isLoading } = useQuery({
    queryKey: ["fleet-deployments", statusFilter],
    queryFn: () => listFleetDeployments(statusFilter || undefined),
    refetchInterval: 15_000,
  });

  const actionMut = useMutation({
    mutationFn: ({ artifactId, deploymentId, action }: { artifactId: string; deploymentId: string; action: "suspend" | "resume" | "terminate" }) =>
      updateDeployment(artifactId, deploymentId, action),
    onSuccess: (_data, vars) => {
      toast.success(`Deployment ${vars.action} requested`);
      queryClient.invalidateQueries({ queryKey: ["fleet-deployments"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Production Deployments</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Fleet-wide view of all production deployments. Auto-refreshes every 15s.
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
              <p className="text-slate-500 font-medium">No production deployments found.</p>
              <p className="text-sm text-slate-400 mt-1">
                Deploy an agent from the Catalog to see it here.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Deployment", "Version", "Status", "Namespace", "Deployed At", "Actions"].map((h) => (
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
                    {/* Deployment */}
                    <td className="px-4 py-3">
                      <Link
                        to={`/catalog/${d.artifact_id}`}
                        className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                      >
                        {d.artifact_name}-{d.id.slice(0, 8)}
                      </Link>
                    </td>

                    {/* Version */}
                    <td className="px-4 py-3 text-slate-600 font-mono text-xs">
                      {d.version_label || "—"}
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

                    {/* Namespace */}
                    <td className="px-4 py-3 text-slate-500 font-mono text-xs">
                      {d.namespace || "—"}
                    </td>

                    {/* Deployed At */}
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      {d.deployed_at
                        ? new Date(d.deployed_at).toLocaleString()
                        : "—"}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        {(d.status === "deploying" || d.status === "suspending" || d.status === "terminating") && (
                          <Loader2 size={14} className="animate-spin text-slate-400" />
                        )}
                        {d.status === "running" && (
                          <>
                            <Link
                              to={`/catalog/${d.artifact_id}/chat`}
                              className="p-1.5 rounded-md hover:bg-blue-50 transition-colors text-blue-600"
                              title="Chat"
                            >
                              <MessageCircle size={14} />
                            </Link>
                            <button
                              onClick={() => actionMut.mutate({ artifactId: d.artifact_id, deploymentId: d.id, action: "suspend" })}
                              disabled={actionMut.isPending}
                              className="p-1.5 rounded-md hover:bg-amber-50 disabled:opacity-40 transition-colors text-amber-600"
                              title="Suspend"
                            >
                              <Pause size={14} />
                            </button>
                          </>
                        )}
                        {d.status === "suspended" && (
                          <button
                            onClick={() => actionMut.mutate({ artifactId: d.artifact_id, deploymentId: d.id, action: "resume" })}
                            disabled={actionMut.isPending}
                            className="p-1.5 rounded-md hover:bg-green-50 disabled:opacity-40 transition-colors text-green-600"
                            title="Resume"
                          >
                            <Play size={14} />
                          </button>
                        )}
                        {(d.status === "running" || d.status === "suspended" || d.status === "failed") && (
                          <button
                            onClick={() => {
                              const verb = d.status === "failed" ? "Clean up" : "Terminate";
                              if (confirm(`${verb} deployment "${d.artifact_name}"? This will delete the K8s namespace and resources.`)) {
                                actionMut.mutate({ artifactId: d.artifact_id, deploymentId: d.id, action: "terminate" });
                              }
                            }}
                            disabled={actionMut.isPending}
                            className="p-1.5 rounded-md hover:bg-red-50 disabled:opacity-40 transition-colors text-red-500"
                            title={d.status === "failed" ? "Clean up failed deployment" : "Terminate"}
                          >
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
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
