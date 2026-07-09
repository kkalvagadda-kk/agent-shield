import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Database, Loader2, Play, RefreshCw, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  createDataset,
  createEvalRun,
  deleteDataset,
  listDatasets,
  listEvalRuns,
  type DatasetItem,
  type EvalRun,
  type PlaygroundDataset,
} from "../api/playgroundApi";
import { listAllDeployments, listAllWorkflowDeployments } from "../api/registryApi";

export default function DatasetsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  // Create dataset modal state
  const [showCreate, setShowCreate] = useState(false);
  const [dsName, setDsName] = useState("");
  const [dsItems, setDsItems] = useState("");

  // Run eval modal state
  const [evalDataset, setEvalDataset] = useState<PlaygroundDataset | null>(null);
  const [evalTargetType, setEvalTargetType] = useState<"agent" | "workflow">("agent");
  const [evalDeploymentId, setEvalDeploymentId] = useState("");
  const [evalWorkflowDepId, setEvalWorkflowDepId] = useState("");

  const { data: datasets, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["playground-datasets"],
    queryFn: listDatasets,
  });

  const { data: evalRuns } = useQuery({
    queryKey: ["eval-runs-all"],
    queryFn: listEvalRuns,
  });

  const { data: agentDeployments } = useQuery({
    queryKey: ["agent-deployments-for-eval"],
    queryFn: () => listAllDeployments("running", 100, "sandbox"),
  });

  const { data: workflowDeployments } = useQuery({
    queryKey: ["workflow-deployments-for-eval"],
    queryFn: () => listAllWorkflowDeployments("running", "sandbox"),
  });

  const createMutation = useMutation({
    mutationFn: (body: { name: string; items: DatasetItem[] }) => createDataset(body),
    onSuccess: () => {
      toast.success("Dataset created.");
      setShowCreate(false);
      setDsName("");
      setDsItems("");
      qc.invalidateQueries({ queryKey: ["playground-datasets"] });
    },
    onError: () => toast.error("Failed to create dataset."),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDataset(id),
    onSuccess: () => {
      toast.success("Dataset deleted.");
      qc.invalidateQueries({ queryKey: ["playground-datasets"] });
    },
    onError: () => toast.error("Failed to delete dataset."),
  });

  const evalMutation = useMutation({
    mutationFn: (body: { dataset_id: string; sandbox_deployment_id?: string; workflow_deployment_id?: string }) =>
      createEvalRun(body),
    onSuccess: (run) => {
      toast.success("Eval run started.");
      setEvalDataset(null);
      setEvalDeploymentId("");
      setEvalWorkflowDepId("");
      navigate(`/playground/eval-runs/${run.id}`);
    },
    onError: () => toast.error("Failed to start eval run."),
  });

  const handleCreate = () => {
    if (!dsName.trim()) {
      toast.error("Dataset name is required.");
      return;
    }
    const items: DatasetItem[] = [];
    for (const line of dsItems.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        items.push(JSON.parse(trimmed) as DatasetItem);
      } catch {
        toast.error(`Invalid JSON on line: ${trimmed.slice(0, 40)}`);
        return;
      }
    }
    createMutation.mutate({ name: dsName.trim(), items });
  };

  const handleRunEval = () => {
    if (evalTargetType === "agent" && !evalDeploymentId) {
      toast.error("Select a deployment.");
      return;
    }
    if (evalTargetType === "workflow" && !evalWorkflowDepId) {
      toast.error("Select a workflow deployment.");
      return;
    }
    if (!evalDataset) return;
    evalMutation.mutate({
      dataset_id: evalDataset.id,
      sandbox_deployment_id: evalTargetType === "agent" ? evalDeploymentId : undefined,
      workflow_deployment_id: evalTargetType === "workflow" ? evalWorkflowDepId : undefined,
    });
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Database size={20} className="text-slate-600" />
            <h1 className="text-2xl font-bold text-slate-900">Datasets</h1>
          </div>
          <p className="text-sm text-slate-500">
            Manage test datasets for playground eval runs.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
          <button onClick={() => setShowCreate(true)} className="btn-primary">
            New Dataset
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading datasets…
        </div>
      )}

      {datasets && (
        <div className="card p-0 overflow-hidden">
          {datasets.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <Database size={36} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">No datasets yet</p>
              <p className="text-slate-400 text-sm mt-1">
                Create a dataset to start running evals.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Name", "Items", "Eval Runs", "Created", "Actions"].map((h) => (
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
                {datasets.map((ds) => (
                  <tr key={ds.id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3 font-medium text-slate-800">{ds.name}</td>
                    <td className="px-4 py-3 text-slate-500">
                      {Array.isArray(ds.items) ? ds.items.length : 0} item
                      {(Array.isArray(ds.items) ? ds.items.length : 0) !== 1 ? "s" : ""}
                    </td>
                    <td className="px-4 py-3">
                      <DatasetEvalRuns runs={(evalRuns ?? []).filter(r => r.dataset_id === ds.id)} navigate={navigate} />
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {new Date(ds.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            setEvalDataset(ds);
                            setEvalDeploymentId("");
                            setEvalWorkflowDepId("");
                          }}
                          className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                        >
                          <Play size={12} />
                          Run Eval
                        </button>
                        <button
                          onClick={() => {
                            if (confirm(`Delete dataset "${ds.name}"?`)) {
                              deleteMutation.mutate(ds.id);
                            }
                          }}
                          disabled={deleteMutation.isPending}
                          className="inline-flex items-center gap-1 text-xs text-red-500 hover:text-red-700 font-medium"
                        >
                          <Trash2 size={12} />
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Create Dataset Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-800">New Dataset</h2>
              <button onClick={() => setShowCreate(false)} className="text-slate-400 hover:text-slate-600">
                <X size={18} />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="label text-xs mb-1">Dataset Name</label>
                <input
                  className="input text-sm"
                  placeholder="e.g. order-lookup-tests"
                  value={dsName}
                  onChange={(e) => setDsName(e.target.value)}
                />
              </div>
              <div>
                <label className="label text-xs mb-1">Items (one JSON object per line)</label>
                <textarea
                  className="input text-xs font-mono h-40 resize-none"
                  placeholder={`{"input": "What is order 123?", "expected_output": "Order 123 is shipped"}\n{"input": "Cancel order 456", "expected_output": "Order 456 cancelled"}`}
                  value={dsItems}
                  onChange={(e) => setDsItems(e.target.value)}
                />
                <p className="text-xs text-slate-400 mt-1">
                  Each line is a JSON object with <code>input</code> and optionally{" "}
                  <code>expected_output</code>.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setShowCreate(false)} className="btn-secondary text-sm">
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={createMutation.isPending}
                className="btn-primary text-sm"
              >
                {createMutation.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  "Create Dataset"
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Run Eval Modal */}
      {evalDataset && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-800">Run Eval</h2>
              <button onClick={() => setEvalDataset(null)} className="text-slate-400 hover:text-slate-600">
                <X size={18} />
              </button>
            </div>
            <p className="text-sm text-slate-500 mb-4">
              Dataset: <span className="font-medium text-slate-700">{evalDataset.name}</span> (
              {Array.isArray(evalDataset.items) ? evalDataset.items.length : 0} items)
            </p>

            {/* Target type toggle */}
            <div className="flex gap-1 p-1 bg-slate-100 rounded-lg mb-4">
              <button
                onClick={() => { setEvalTargetType("agent"); setEvalWorkflowDepId(""); }}
                className={`flex-1 text-xs font-medium py-1.5 rounded-md transition-colors ${
                  evalTargetType === "agent" ? "bg-white shadow text-slate-800" : "text-slate-500"
                }`}
              >
                Agent Deployment
              </button>
              <button
                onClick={() => { setEvalTargetType("workflow"); setEvalDeploymentId(""); }}
                className={`flex-1 text-xs font-medium py-1.5 rounded-md transition-colors ${
                  evalTargetType === "workflow" ? "bg-white shadow text-slate-800" : "text-slate-500"
                }`}
              >
                Workflow Deployment
              </button>
            </div>

            {evalTargetType === "agent" ? (
              <div>
                <label className="label text-xs mb-1">Select Deployment</label>
                <select
                  className="input text-sm"
                  value={evalDeploymentId}
                  onChange={(e) => setEvalDeploymentId(e.target.value)}
                >
                  <option value="">-- pick a running deployment --</option>
                  {agentDeployments?.items.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name ?? d.agent_name ?? d.id.slice(0, 8)} ({d.agent_name})
                    </option>
                  ))}
                </select>
                {agentDeployments?.items.length === 0 && (
                  <p className="text-xs text-amber-600 mt-1">No running sandbox deployments found. Deploy an agent first.</p>
                )}
              </div>
            ) : (
              <div>
                <label className="label text-xs mb-1">Select Workflow Deployment</label>
                <select
                  className="input text-sm"
                  value={evalWorkflowDepId}
                  onChange={(e) => setEvalWorkflowDepId(e.target.value)}
                >
                  <option value="">-- pick a running workflow deployment --</option>
                  {workflowDeployments?.map((wd) => (
                    <option key={wd.id} value={wd.id}>
                      {wd.name ?? wd.workflow_name ?? wd.id.slice(0, 8)} ({wd.workflow_name})
                    </option>
                  ))}
                </select>
                {workflowDeployments?.length === 0 && (
                  <p className="text-xs text-amber-600 mt-1">No running sandbox workflow deployments found.</p>
                )}
              </div>
            )}

            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setEvalDataset(null)} className="btn-secondary text-sm">
                Cancel
              </button>
              <button
                onClick={handleRunEval}
                disabled={evalMutation.isPending || (evalTargetType === "agent" ? !evalDeploymentId : !evalWorkflowDepId)}
                className="btn-primary text-sm"
              >
                {evalMutation.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  "Start Eval"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DatasetEvalRuns({ runs, navigate }: { runs: EvalRun[]; navigate: (path: string) => void }) {
  if (runs.length === 0) {
    return <span className="text-xs text-slate-300">—</span>;
  }
  const sorted = [...runs].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  const recent = sorted.slice(0, 3);
  return (
    <div className="space-y-1">
      {recent.map((r) => (
        <button
          key={r.id}
          onClick={() => navigate(`/playground/eval-runs/${r.id}`)}
          className="flex items-center gap-1.5 text-xs hover:bg-slate-100 rounded px-1 py-0.5 -mx-1 w-full text-left"
        >
          <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${
            r.status === "completed" && r.overall_score != null && r.overall_score >= 0.7
              ? "bg-green-500"
              : r.status === "completed"
                ? "bg-amber-500"
                : r.status === "failed"
                  ? "bg-red-500"
                  : "bg-slate-300"
          }`} />
          <span className="text-slate-600 truncate">{r.agent_name}</span>
          {r.overall_score != null && (
            <span className="text-slate-400 ml-auto shrink-0">{Math.round(r.overall_score * 100)}%</span>
          )}
        </button>
      ))}
      {sorted.length > 3 && (
        <span className="text-[10px] text-slate-400">+{sorted.length - 3} more</span>
      )}
    </div>
  );
}
