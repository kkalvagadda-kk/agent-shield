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
  type DatasetItem,
  type PlaygroundDataset,
} from "../api/playgroundApi";
import { listAgents } from "../api/registryApi";

export default function DatasetsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  // Create dataset modal state
  const [showCreate, setShowCreate] = useState(false);
  const [dsName, setDsName] = useState("");
  const [dsItems, setDsItems] = useState("");

  // Run eval modal state
  const [evalDataset, setEvalDataset] = useState<PlaygroundDataset | null>(null);
  const [evalAgent, setEvalAgent] = useState("");

  const { data: datasets, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["playground-datasets"],
    queryFn: listDatasets,
  });

  const { data: agentsData } = useQuery({
    queryKey: ["agents-for-eval"],
    queryFn: () => listAgents(100, 0, "active"),
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
    mutationFn: ({ agent_name, dataset_id }: { agent_name: string; dataset_id: string }) =>
      createEvalRun({ agent_name, dataset_id }),
    onSuccess: (run) => {
      toast.success("Eval run started.");
      setEvalDataset(null);
      setEvalAgent("");
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
    if (!evalAgent) {
      toast.error("Select an agent.");
      return;
    }
    if (!evalDataset) return;
    evalMutation.mutate({ agent_name: evalAgent, dataset_id: evalDataset.id });
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
                  {["Name", "Items", "Created", "Actions"].map((h) => (
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
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {new Date(ds.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            setEvalDataset(ds);
                            setEvalAgent("");
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
            <div>
              <label className="label text-xs mb-1">Select Agent</label>
              <select
                className="input text-sm"
                value={evalAgent}
                onChange={(e) => setEvalAgent(e.target.value)}
              >
                <option value="">-- pick an agent --</option>
                {agentsData?.items.map((a) => (
                  <option key={a.id} value={a.name}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => setEvalDataset(null)} className="btn-secondary text-sm">
                Cancel
              </button>
              <button
                onClick={handleRunEval}
                disabled={evalMutation.isPending || !evalAgent}
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
