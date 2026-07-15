import { useState, type Dispatch, type SetStateAction } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Database, Loader2, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import {
  createDataset,
  createEvalRun,
  deleteDataset,
  listDatasets,
  listEvalRuns,
  type AnyDatasetItem,
  type DatasetItem,
  type DatasetMode,
  type DurableDatasetItem,
  type EvalRun,
  type ExpectedTrajectoryStep,
  type PlaygroundDataset,
  type SideEffectAssertion,
  type TrajectoryMatchMode,
  type WorkflowDatasetItem,
} from "../api/playgroundApi";
import { listAllDeployments, listAllWorkflowDeployments } from "../api/registryApi";

export default function DatasetsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  // Create dataset modal state
  const [showCreate, setShowCreate] = useState(false);
  const [dsName, setDsName] = useState("");
  const [dsMode, setDsMode] = useState<DatasetMode>("reactive");
  const [dsItems, setDsItems] = useState("");

  // Durable item editor state (E-1). A durable dataset is authored as a single
  // trajectory item: an input_payload + an optional expected_trajectory (+ E-2's
  // optional expected_side_effects).
  const [durInputPayload, setDurInputPayload] = useState("");
  const [durMatchMode, setDurMatchMode] = useState<TrajectoryMatchMode>("superset");
  const [durSteps, setDurSteps] = useState<DurableStepDraft[]>([]);
  const [durSideEffects, setDurSideEffects] = useState<SideEffectDraft[]>([]);

  // Workflow item editor state (E-5). A workflow dataset is authored as a single
  // run-tree item: an input + an ordered expected_member_path (+ optional
  // per-member rubric), scored against the real workflow run tree.
  const [wfInput, setWfInput] = useState("");
  const [wfExpectedOutput, setWfExpectedOutput] = useState("");
  const [wfMatchMode, setWfMatchMode] = useState<TrajectoryMatchMode>("ordered");
  const [wfMemberPath, setWfMemberPath] = useState<string[]>([]);
  const [wfPerMember, setWfPerMember] = useState<PerMemberDraft[]>([]);

  const resetCreateForm = () => {
    setDsName("");
    setDsMode("reactive");
    setDsItems("");
    setDurInputPayload("");
    setDurMatchMode("superset");
    setDurSteps([]);
    setDurSideEffects([]);
    setWfInput("");
    setWfExpectedOutput("");
    setWfMatchMode("ordered");
    setWfMemberPath([]);
    setWfPerMember([]);
  };

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
    mutationFn: (body: { name: string; items: AnyDatasetItem[]; mode: DatasetMode }) =>
      createDataset(body),
    onSuccess: () => {
      toast.success("Dataset created.");
      setShowCreate(false);
      resetCreateForm();
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
    // Reactive: one JSON object per line. Durable (E-1): a structured trajectory
    // editor. Other modes create an empty dataset (their editors land later).
    let items: AnyDatasetItem[] = [];
    if (dsMode === "reactive") {
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
    } else if (dsMode === "durable") {
      const built = buildDurableItem(durInputPayload, durMatchMode, durSteps, durSideEffects);
      if ("error" in built) {
        toast.error(built.error);
        return;
      }
      items = [built.item];
    } else if (dsMode === "workflow") {
      const built = buildWorkflowItem(
        wfInput,
        wfExpectedOutput,
        wfMatchMode,
        wfMemberPath,
        wfPerMember,
      );
      if ("error" in built) {
        toast.error(built.error);
        return;
      }
      items = [built.item];
    }
    createMutation.mutate({ name: dsName.trim(), items, mode: dsMode });
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
                    <td className="px-4 py-3 font-medium text-slate-800">
                      {ds.name}
                      {ds.mode && ds.mode !== "reactive" && (
                        <span className="ml-2 inline-block px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-500 uppercase tracking-wide">
                          {ds.mode}
                        </span>
                      )}
                    </td>
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          {/* max-h + overflow so a long form (workflow mode with several members +
              per-member rubrics) never pushes the Create button below the fold on a
              normal-height screen — the modal body scrolls instead of clipping. */}
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-800">New Dataset</h2>
              <button onClick={() => { setShowCreate(false); resetCreateForm(); }} className="text-slate-400 hover:text-slate-600">
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
                <label htmlFor="dataset-mode" className="label text-xs mb-1">
                  Mode
                </label>
                <select
                  id="dataset-mode"
                  aria-label="Dataset mode"
                  className="input text-sm"
                  value={dsMode}
                  onChange={(e) => setDsMode(e.target.value as DatasetMode)}
                >
                  <option value="reactive">Reactive (response correctness)</option>
                  <option value="durable">Durable (trajectory)</option>
                  <option value="scheduled">Scheduled (side-effects)</option>
                  <option value="webhook">Webhook (filter + action)</option>
                  <option value="workflow">Workflow (run-tree)</option>
                </select>
                <p className="text-xs text-slate-400 mt-1">
                  The eval family this dataset scores. Defaults to{" "}
                  <code>reactive</code>.
                </p>
              </div>
              {dsMode === "reactive" ? (
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
              ) : dsMode === "durable" ? (
                <DurableItemEditor
                  inputPayload={durInputPayload}
                  setInputPayload={setDurInputPayload}
                  matchMode={durMatchMode}
                  setMatchMode={setDurMatchMode}
                  steps={durSteps}
                  setSteps={setDurSteps}
                  sideEffects={durSideEffects}
                  setSideEffects={setDurSideEffects}
                />
              ) : dsMode === "workflow" ? (
                <WorkflowItemEditor
                  input={wfInput}
                  setInput={setWfInput}
                  expectedOutput={wfExpectedOutput}
                  setExpectedOutput={setWfExpectedOutput}
                  matchMode={wfMatchMode}
                  setMatchMode={setWfMatchMode}
                  memberPath={wfMemberPath}
                  setMemberPath={setWfMemberPath}
                  perMember={wfPerMember}
                  setPerMember={setWfPerMember}
                />
              ) : (
                <div>
                  <label className="label text-xs mb-1">Items</label>
                  <textarea
                    disabled
                    aria-label="Items editor (disabled)"
                    className="input text-xs font-mono h-40 resize-none opacity-50 cursor-not-allowed"
                    placeholder=""
                    value=""
                  />
                  <p className="text-xs text-amber-600 mt-1">
                    The <code>{dsMode}</code> item editor is coming later. You can
                    create an empty {dsMode} dataset now and add items later.
                  </p>
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button onClick={() => { setShowCreate(false); resetCreateForm(); }} className="btn-secondary text-sm">
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

// A single expected-trajectory step as edited in the form (args_match kept as
// raw JSON text until validated on save).
interface DurableStepDraft {
  tool: string;
  argsMatch: string;
  expectApproval: boolean;
}

// A single expected SIDE EFFECT as edited in the form (E-2). `argsMatch` is raw
// JSON text until validated on save; `count` is text so the field can be emptied
// while typing.
interface SideEffectDraft {
  tool: string;
  argsMatch: string;
  occurs: SideEffectOccurs;
  count: string;
}

type SideEffectOccurs = NonNullable<SideEffectAssertion["occurs"]>;

const MATCH_MODES: TrajectoryMatchMode[] = ["exact", "ordered", "superset", "unordered"];
const OCCURS_MODES: SideEffectOccurs[] = ["exactly", "at_least", "never"];

/**
 * Structured editor for a durable dataset item (E-1): an `input_payload` plus an
 * optional `expected_trajectory` (ordered tool steps with per-step `args_match`
 * and an `expect_approval` HITL toggle), plus E-2's optional
 * `expected_side_effects`. Rendered only when the dataset mode is `durable`.
 * Validation happens on save (`buildDurableItem`).
 */
function DurableItemEditor({
  inputPayload,
  setInputPayload,
  matchMode,
  setMatchMode,
  steps,
  setSteps,
  sideEffects,
  setSideEffects,
}: {
  inputPayload: string;
  setInputPayload: (v: string) => void;
  matchMode: TrajectoryMatchMode;
  setMatchMode: (v: TrajectoryMatchMode) => void;
  steps: DurableStepDraft[];
  setSteps: Dispatch<SetStateAction<DurableStepDraft[]>>;
  sideEffects: SideEffectDraft[];
  setSideEffects: Dispatch<SetStateAction<SideEffectDraft[]>>;
}) {
  const updateStep = (idx: number, patch: Partial<DurableStepDraft>) =>
    setSteps((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  const addStep = () =>
    setSteps((prev) => [...prev, { tool: "", argsMatch: "", expectApproval: false }]);
  const removeStep = (idx: number) =>
    setSteps((prev) => prev.filter((_, i) => i !== idx));

  const updateSideEffect = (idx: number, patch: Partial<SideEffectDraft>) =>
    setSideEffects((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  const addSideEffect = () =>
    setSideEffects((prev) => [
      ...prev,
      { tool: "", argsMatch: "", occurs: "exactly", count: "1" },
    ]);
  const removeSideEffect = (idx: number) =>
    setSideEffects((prev) => prev.filter((_, i) => i !== idx));

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="durable-input-payload" className="label text-xs mb-1">
          Input Payload (JSON object)
        </label>
        <textarea
          id="durable-input-payload"
          aria-label="Durable input payload"
          className="input text-xs font-mono h-24 resize-none"
          placeholder={`{"contract_url": "s3://demo/acme.pdf"}`}
          value={inputPayload}
          onChange={(e) => setInputPayload(e.target.value)}
        />
        <p className="text-xs text-slate-400 mt-1">
          Fed to the durable run as <code>input_payload</code>.
        </p>
      </div>

      <div>
        <label htmlFor="durable-match-mode" className="label text-xs mb-1">
          Trajectory Match Mode
        </label>
        <select
          id="durable-match-mode"
          aria-label="Trajectory match mode"
          className="input text-sm"
          value={matchMode}
          onChange={(e) => setMatchMode(e.target.value as TrajectoryMatchMode)}
        >
          {MATCH_MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <p className="text-xs text-slate-400 mt-1">
          How the expected tool sequence is compared to the real run steps.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label text-xs">Expected Trajectory Steps</label>
          <button
            type="button"
            onClick={addStep}
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            <Plus size={12} />
            Add step
          </button>
        </div>
        {steps.length === 0 ? (
          <p className="text-xs text-slate-400">
            No steps — a reference-free durable item scored on the response only.
          </p>
        ) : (
          <div className="space-y-2">
            {steps.map((s, i) => (
              <div
                key={i}
                data-testid={`durable-step-${i}`}
                className="rounded-lg border border-slate-200 p-2 space-y-2"
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-400 w-4">{i + 1}.</span>
                  <input
                    aria-label={`Step ${i + 1} tool`}
                    className="input text-xs flex-1"
                    placeholder="tool name (e.g. jira_create)"
                    value={s.tool}
                    onChange={(e) => updateStep(i, { tool: e.target.value })}
                  />
                  <button
                    type="button"
                    aria-label={`Remove step ${i + 1}`}
                    onClick={() => removeStep(i)}
                    className="text-slate-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <input
                  aria-label={`Step ${i + 1} args match`}
                  className="input text-xs font-mono"
                  placeholder={`args match (JSON, optional) e.g. {"project": "LEG"}`}
                  value={s.argsMatch}
                  onChange={(e) => updateStep(i, { argsMatch: e.target.value })}
                />
                <label className="flex items-center gap-2 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    aria-label={`Step ${i + 1} expect approval`}
                    checked={s.expectApproval}
                    onChange={(e) => updateStep(i, { expectApproval: e.target.checked })}
                  />
                  Expect approval (HITL — this step should park)
                </label>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Expected side effects (E-2). Authoring ANY assertion here is what makes
          the eval-runner launch this item under `eval_mode=record`, so the write
          tools are recorded + mocked instead of really firing. */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label text-xs">Expected Side Effects</label>
          <button
            type="button"
            onClick={addSideEffect}
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            <Plus size={12} />
            Add side effect
          </button>
        </div>
        {sideEffects.length === 0 ? (
          <p className="text-xs text-slate-400">
            None — this item runs <code>live</code> and its tool calls are delivered
            for real. Add one to run it in <code>record</code> mode: side-effecting
            calls are recorded and mocked instead of really sending.
          </p>
        ) : (
          <>
            <p className="text-xs text-amber-600 mb-2">
              This item will run in <code>record</code> mode — no real emails, tickets,
              or payments are sent.
            </p>
            <div className="space-y-2">
              {sideEffects.map((s, i) => (
                <div
                  key={i}
                  data-testid={`side-effect-${i}`}
                  className="rounded-lg border border-slate-200 p-2 space-y-2"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-400 w-4">{i + 1}.</span>
                    <input
                      aria-label={`Side effect ${i + 1} tool`}
                      className="input text-xs flex-1"
                      placeholder="tool name (e.g. send_email)"
                      value={s.tool}
                      onChange={(e) => updateSideEffect(i, { tool: e.target.value })}
                    />
                    <button
                      type="button"
                      aria-label={`Remove side effect ${i + 1}`}
                      onClick={() => removeSideEffect(i)}
                      className="text-slate-400 hover:text-red-500"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <input
                    aria-label={`Side effect ${i + 1} args match`}
                    className="input text-xs font-mono"
                    placeholder={`args match (JSON, optional) e.g. {"to": "compliance@acme.com"}`}
                    value={s.argsMatch}
                    onChange={(e) => updateSideEffect(i, { argsMatch: e.target.value })}
                  />
                  <div className="flex items-center gap-2">
                    <select
                      aria-label={`Side effect ${i + 1} occurs`}
                      className="input text-xs flex-1"
                      value={s.occurs}
                      onChange={(e) =>
                        updateSideEffect(i, { occurs: e.target.value as SideEffectOccurs })
                      }
                    >
                      {OCCURS_MODES.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                    {s.occurs !== "never" && (
                      <input
                        aria-label={`Side effect ${i + 1} count`}
                        type="number"
                        min={1}
                        className="input text-xs w-20"
                        value={s.count}
                        onChange={(e) => updateSideEffect(i, { count: e.target.value })}
                      />
                    )}
                  </div>
                  <p className="text-[10px] text-slate-400">
                    {s.occurs === "never"
                      ? "The run must NEVER make this call — any recorded match fails the item."
                      : `The run must make this call ${s.occurs === "at_least" ? "at least" : "exactly"} ${s.count || "1"} time(s).`}
                  </p>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Validate + assemble the durable dataset item from the trajectory editor.
 * Rejects a malformed `input_payload`, `args_match`, or side-effect assertion
 * before any POST — a reference-free durable item (no steps, no side effects) is
 * legal and degrades to the response dimension server-side.
 *
 * This is the function that puts `expected_side_effects` on the POST — the field
 * whose presence makes the eval-runner launch the item under `eval_mode=record`.
 */
export function buildDurableItem(
  inputPayloadStr: string,
  matchMode: TrajectoryMatchMode,
  steps: DurableStepDraft[],
  sideEffects: SideEffectDraft[] = [],
): { item: DurableDatasetItem } | { error: string } {
  const trimmedPayload = inputPayloadStr.trim();
  if (!trimmedPayload) {
    return { error: "Input payload is required for a durable item." };
  }
  let inputPayload: Record<string, unknown>;
  try {
    const parsed = JSON.parse(trimmedPayload);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { error: "Input payload must be a JSON object." };
    }
    inputPayload = parsed as Record<string, unknown>;
  } catch {
    return { error: "Input payload is not valid JSON." };
  }

  const builtSteps: ExpectedTrajectoryStep[] = [];
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    const tool = s.tool.trim();
    if (!tool) {
      return { error: `Step ${i + 1}: tool name is required.` };
    }
    const step: ExpectedTrajectoryStep = { tool };
    const rawArgs = s.argsMatch.trim();
    if (rawArgs) {
      try {
        const parsed = JSON.parse(rawArgs);
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          return { error: `Step ${i + 1}: args match must be a JSON object.` };
        }
        step.args_match = parsed as Record<string, unknown>;
      } catch {
        return { error: `Step ${i + 1}: args match is not valid JSON.` };
      }
    }
    if (s.expectApproval) step.expect_approval = true;
    builtSteps.push(step);
  }

  const builtSideEffects: SideEffectAssertion[] = [];
  for (let i = 0; i < sideEffects.length; i++) {
    const s = sideEffects[i];
    const tool = s.tool.trim();
    if (!tool) {
      return { error: `Side effect ${i + 1}: tool name is required.` };
    }
    const assertion: SideEffectAssertion = { tool, occurs: s.occurs };
    const rawArgs = s.argsMatch.trim();
    if (rawArgs) {
      try {
        const parsed = JSON.parse(rawArgs);
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          return { error: `Side effect ${i + 1}: args match must be a JSON object.` };
        }
        assertion.args_match = parsed as Record<string, unknown>;
      } catch {
        return { error: `Side effect ${i + 1}: args match is not valid JSON.` };
      }
    }
    // `never` is a pure absence assertion — a count on it is meaningless, so it is
    // not sent (the scorer ignores count for never; omitting keeps the item honest).
    if (s.occurs !== "never") {
      const count = Number(s.count);
      if (!Number.isInteger(count) || count < 1) {
        return { error: `Side effect ${i + 1}: count must be a whole number ≥ 1.` };
      }
      assertion.count = count;
    }
    builtSideEffects.push(assertion);
  }

  const item: DurableDatasetItem = { kind: "durable", input_payload: inputPayload };
  if (builtSteps.length > 0) {
    item.expected_trajectory = { match_mode: matchMode, steps: builtSteps };
  }
  if (builtSideEffects.length > 0) {
    item.expected_side_effects = builtSideEffects;
  }
  return { item };
}

// A single per-member expectation as edited in the form.
interface PerMemberDraft {
  member: string;
  rubric: string;
}

/**
 * Structured editor for a workflow dataset item (E-5): an `input` plus an ordered
 * `expected_member_path` (which members should run, in order) with a `match_mode`,
 * an optional `expected_output`, and an optional `per_member` rubric map. Rendered
 * only when the dataset mode is `workflow`. Validation happens on save
 * (`buildWorkflowItem`), which is what sends `expected_member_path` on the POST.
 */
function WorkflowItemEditor({
  input,
  setInput,
  expectedOutput,
  setExpectedOutput,
  matchMode,
  setMatchMode,
  memberPath,
  setMemberPath,
  perMember,
  setPerMember,
}: {
  input: string;
  setInput: (v: string) => void;
  expectedOutput: string;
  setExpectedOutput: (v: string) => void;
  matchMode: TrajectoryMatchMode;
  setMatchMode: (v: TrajectoryMatchMode) => void;
  memberPath: string[];
  setMemberPath: Dispatch<SetStateAction<string[]>>;
  perMember: PerMemberDraft[];
  setPerMember: Dispatch<SetStateAction<PerMemberDraft[]>>;
}) {
  const updateMember = (idx: number, value: string) =>
    setMemberPath((prev) => prev.map((m, i) => (i === idx ? value : m)));
  const addMember = () => setMemberPath((prev) => [...prev, ""]);
  const removeMember = (idx: number) =>
    setMemberPath((prev) => prev.filter((_, i) => i !== idx));

  const updatePerMember = (idx: number, patch: Partial<PerMemberDraft>) =>
    setPerMember((prev) => prev.map((p, i) => (i === idx ? { ...p, ...patch } : p)));
  const addPerMember = () =>
    setPerMember((prev) => [...prev, { member: "", rubric: "" }]);
  const removePerMember = (idx: number) =>
    setPerMember((prev) => prev.filter((_, i) => i !== idx));

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="workflow-input" className="label text-xs mb-1">
          Input Message
        </label>
        <textarea
          id="workflow-input"
          aria-label="Workflow input message"
          className="input text-xs h-20 resize-none"
          placeholder="e.g. My refund for order 123 never arrived"
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <p className="text-xs text-slate-400 mt-1">
          Fed to the workflow run as <code>input_message</code>.
        </p>
      </div>

      <div>
        <label htmlFor="workflow-expected-output" className="label text-xs mb-1">
          Expected Output (optional)
        </label>
        <textarea
          id="workflow-expected-output"
          aria-label="Workflow expected output"
          className="input text-xs h-16 resize-none"
          placeholder="The workflow's final answer (scored on the response dimension)."
          value={expectedOutput}
          onChange={(e) => setExpectedOutput(e.target.value)}
        />
      </div>

      <div>
        <label htmlFor="workflow-match-mode" className="label text-xs mb-1">
          Member-Path Match Mode
        </label>
        <select
          id="workflow-match-mode"
          aria-label="Member path match mode"
          className="input text-sm"
          value={matchMode}
          onChange={(e) => setMatchMode(e.target.value as TrajectoryMatchMode)}
        >
          {MATCH_MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <p className="text-xs text-slate-400 mt-1">
          How the expected member path is compared to the members that actually ran.
        </p>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label text-xs">Expected Member Path (in order)</label>
          <button
            type="button"
            onClick={addMember}
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            <Plus size={12} />
            Add member
          </button>
        </div>
        {memberPath.length === 0 ? (
          <p className="text-xs text-slate-400">
            No members — a reference-free workflow item scored on the response only.
          </p>
        ) : (
          <div className="space-y-2">
            {memberPath.map((m, i) => (
              <div
                key={i}
                data-testid={`member-${i}`}
                className="flex items-center gap-2"
              >
                <span className="text-xs text-slate-400 w-4">{i + 1}.</span>
                <input
                  aria-label={`Member ${i + 1} name`}
                  className="input text-xs flex-1"
                  placeholder="member (agent) name — e.g. triage"
                  value={m}
                  onChange={(e) => updateMember(i, e.target.value)}
                />
                <button
                  type="button"
                  aria-label={`Remove member ${i + 1}`}
                  onClick={() => removeMember(i)}
                  className="text-slate-400 hover:text-red-500"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label text-xs">Per-Member Rubric (optional)</label>
          <button
            type="button"
            onClick={addPerMember}
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
          >
            <Plus size={12} />
            Add rubric
          </button>
        </div>
        {perMember.length === 0 ? (
          <p className="text-xs text-slate-400">
            No per-member rubric — members are scored at the path + response level.
          </p>
        ) : (
          <div className="space-y-2">
            {perMember.map((p, i) => (
              <div
                key={i}
                data-testid={`per-member-${i}`}
                className="rounded-lg border border-slate-200 p-2 space-y-2"
              >
                <div className="flex items-center gap-2">
                  <input
                    aria-label={`Per-member ${i + 1} name`}
                    className="input text-xs flex-1"
                    placeholder="member name (e.g. triage)"
                    value={p.member}
                    onChange={(e) => updatePerMember(i, { member: e.target.value })}
                  />
                  <button
                    type="button"
                    aria-label={`Remove per-member ${i + 1}`}
                    onClick={() => removePerMember(i)}
                    className="text-slate-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <input
                  aria-label={`Per-member ${i + 1} rubric`}
                  className="input text-xs"
                  placeholder="rubric — e.g. correctly routed to billing"
                  value={p.rubric}
                  onChange={(e) => updatePerMember(i, { rubric: e.target.value })}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Validate + assemble the workflow dataset item from the run-tree editor. Rejects a
 * missing input, a blank member row, or an incomplete per-member rubric before any
 * POST. A reference-free item (no members) is legal and degrades to the response
 * dimension server-side. This is the function that puts `expected_member_path` on
 * the POST body (the save→reload round-trip).
 */
export function buildWorkflowItem(
  inputStr: string,
  expectedOutputStr: string,
  matchMode: TrajectoryMatchMode,
  memberPath: string[],
  perMember: PerMemberDraft[],
): { item: WorkflowDatasetItem } | { error: string } {
  const input = inputStr.trim();
  if (!input) {
    return { error: "Input message is required for a workflow item." };
  }

  const members: string[] = [];
  for (let i = 0; i < memberPath.length; i++) {
    const name = memberPath[i].trim();
    if (!name) {
      return { error: `Member ${i + 1}: name is required.` };
    }
    members.push(name);
  }

  const perMemberMap: Record<string, { rubric: string }> = {};
  for (let i = 0; i < perMember.length; i++) {
    const member = perMember[i].member.trim();
    const rubric = perMember[i].rubric.trim();
    if (!member) {
      return { error: `Per-member rubric ${i + 1}: member name is required.` };
    }
    if (!rubric) {
      return { error: `Per-member rubric for "${member}": rubric text is required.` };
    }
    perMemberMap[member] = { rubric };
  }

  const item: WorkflowDatasetItem = { kind: "workflow", input_message: input };
  const expectedOutput = expectedOutputStr.trim();
  if (expectedOutput) item.expected_output = expectedOutput;
  if (members.length > 0) {
    item.expected_member_path = members;
    item.match_mode = matchMode;
  }
  if (Object.keys(perMemberMap).length > 0) item.per_member = perMemberMap;
  return { item };
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
