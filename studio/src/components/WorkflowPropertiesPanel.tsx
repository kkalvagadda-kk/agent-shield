import { useQuery, useMutation } from "@tanstack/react-query";
import { Settings, ExternalLink, AlertTriangle, Save, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getAgent, updateAgent } from "../api/registryApi";
import { useWorkflowStore } from "../stores/workflowStore";
import type { WorkflowMemberNodeData } from "../nodes/WorkflowMemberNode";

// ---------------------------------------------------------------------------
// Field helper
// ---------------------------------------------------------------------------
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

// Role + orchestration config editable for ANY member (inline or existing) —
// role/max_iterations are workflow-membership attributes, persisted on save.
function MemberRoleFields({ nodeId, data }: { nodeId: string; data: WorkflowMemberNodeData }) {
  const store = useWorkflowStore();
  const role = data.role ?? "";
  const routing = (data.routing ?? {}) as Record<string, unknown>;
  const maxIters = routing.max_iterations;

  return (
    <>
      <Field label="Role (for supervisor orchestration)">
        <select
          className="input"
          value={role}
          onChange={(e) => store.updateNodeData(nodeId, { role: e.target.value || undefined })}
        >
          <option value="">— none —</option>
          <option value="supervisor">supervisor</option>
          <option value="worker">worker</option>
        </select>
      </Field>
      {role === "supervisor" && (
        <Field label="Max iterations">
          <input
            type="number"
            min={1}
            className="input"
            value={maxIters === undefined ? "" : String(maxIters)}
            onChange={(e) =>
              store.updateNodeData(nodeId, {
                routing: { ...routing, max_iterations: e.target.value ? Number(e.target.value) : undefined },
              })
            }
            placeholder="10"
          />
          <p className="text-xs text-slate-400 mt-1">
            Supervisor loop stops after this many turns (default 10) or when its output says DONE.
          </p>
        </Field>
      )}
    </>
  );
}

// Inline agent: full editing (instructions/description/model) written back to
// the real agent via updateAgent. Marks the node dirty until saved.
function InlineAgentPanel({ nodeId, data }: { nodeId: string; data: WorkflowMemberNodeData }) {
  const store = useWorkflowStore();
  const agentName = data.agent_name;

  const { data: agent } = useQuery({
    queryKey: ["agent", agentName],
    queryFn: () => getAgent(agentName),
    enabled: !!agentName,
  });

  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [model, setModel] = useState("claude-sonnet-4-6");
  const hydrated = useRef(false);

  useEffect(() => {
    if (agent && !hydrated.current) {
      const meta = (agent.metadata ?? {}) as Record<string, unknown>;
      setDescription(agent.description ?? "");
      setInstructions(String(meta.instructions ?? ""));
      setModel(String(meta.model ?? "claude-sonnet-4-6"));
      hydrated.current = true;
    }
  }, [agent]);

  const save = useMutation({
    mutationFn: () => {
      const meta = { ...((agent?.metadata ?? {}) as Record<string, unknown>), instructions, model };
      return updateAgent(agentName, { description, metadata: meta });
    },
    onSuccess: () => store.updateNodeData(nodeId, { inline_dirty: false }),
  });

  const markDirty = () => store.updateNodeData(nodeId, { inline_dirty: true });

  return (
    <>
      <div className="mb-3 flex items-start gap-2 rounded-md bg-amber-50 border border-amber-200 px-2.5 py-2">
        <AlertTriangle size={14} className="text-amber-500 shrink-0 mt-0.5" />
        <p className="text-xs text-amber-700">
          This agent was created inline and isn&apos;t deployed. It must be deployed before the
          workflow can run it —{" "}
          <a href={`/agents/${agentName}/deploy`} className="underline hover:text-amber-900">
            deploy it
          </a>
          .
        </p>
      </div>

      <Field label="Description">
        <input
          className="input"
          value={description}
          onChange={(e) => { setDescription(e.target.value); markDirty(); }}
          placeholder="What does this agent do?"
        />
      </Field>
      <Field label="Instructions">
        <textarea
          className="input resize-none"
          rows={5}
          value={instructions}
          onChange={(e) => { setInstructions(e.target.value); markDirty(); }}
          placeholder="System prompt for this agent…"
        />
      </Field>
      <Field label="Model">
        <select
          className="input"
          value={model}
          onChange={(e) => { setModel(e.target.value); markDirty(); }}
        >
          <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
          <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
          <option value="claude-opus-4-8">claude-opus-4-8</option>
        </select>
      </Field>

      <button
        onClick={() => save.mutate()}
        disabled={save.isPending}
        className="btn-primary w-full text-sm mb-4"
      >
        {save.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
        {save.isPending ? "Saving…" : "Save agent config"}
      </button>

      <p className="text-xs text-slate-400 mb-3">
        Tools &amp; skills for this agent are managed on its{" "}
        <a href={`/agents/${agentName}`} className="underline hover:text-slate-600">agent page</a>.
      </p>

      <MemberRoleFields nodeId={nodeId} data={data} />
    </>
  );
}

// Existing registered agent: read-only summary + link. Only role/routing editable.
function ExistingAgentPanel({ nodeId, data }: { nodeId: string; data: WorkflowMemberNodeData }) {
  const agentName = data.agent_name;
  const { data: agent } = useQuery({
    queryKey: ["agent", agentName],
    queryFn: () => getAgent(agentName),
    enabled: !!agentName,
  });

  return (
    <>
      <div className="mb-3 rounded-md border border-slate-200 bg-slate-50 p-3">
        <p className="text-sm font-medium text-slate-800">{agentName}</p>
        {agent && (
          <div className="mt-1 space-y-0.5 text-xs text-slate-500">
            <p>Team: {agent.team}</p>
            <p>Shape: {agent.execution_shape ?? "reactive"}</p>
            {agent.description && <p className="text-slate-600">{agent.description}</p>}
          </div>
        )}
        <a
          href={`/agents/${agentName}`}
          className="mt-2 inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
        >
          Edit in Agents <ExternalLink size={11} />
        </a>
      </div>
      <p className="text-xs text-slate-400 mb-3">
        This is an existing shared agent — edit its config on its own page to avoid affecting other
        workflows. Role &amp; orchestration below apply only to this workflow.
      </p>
      <MemberRoleFields nodeId={nodeId} data={data} />
    </>
  );
}

// Edge condition editor (mirrors the Agent Graph EdgePanel; adds the DSL hint).
function EdgePanel({ edgeId }: { edgeId: string }) {
  const store = useWorkflowStore();
  const edge = store.edges.find((e) => e.id === edgeId);
  const condition = String((edge?.data as Record<string, unknown> | undefined)?.condition ?? "");

  return (
    <Field label="Condition">
      <input
        className="input"
        value={condition}
        onChange={(e) => store.updateEdgeCondition(edgeId, e.target.value)}
        placeholder="approved  ·  or blank = default path"
      />
      <p className="text-xs text-slate-400 mt-1">
        Routes to this target when the source agent&apos;s output matches. Use a keyword (output
        contains it), a JSON predicate array (<code>[{'{'}"field":"status","op":"eq","value":"ok"{'}'}]</code>),
        or leave blank for the default (fallback) path.
      </p>
    </Field>
  );
}

export default function WorkflowPropertiesPanel() {
  const store = useWorkflowStore();
  const selectedNode = store.nodes.find((n) => n.id === store.selectedNodeId);
  const data = selectedNode?.data as WorkflowMemberNodeData | undefined;

  const badgeLabel = store.selectedEdgeId
    ? "edge"
    : data?.is_inline
      ? "new agent"
      : selectedNode
        ? "agent"
        : null;

  return (
    <div className="w-72 border-l border-slate-200 bg-white flex flex-col overflow-hidden shrink-0">
      <div className="px-4 py-3 border-b border-slate-200 flex items-center gap-2">
        <Settings size={14} className="text-slate-500" />
        <span className="text-sm font-semibold text-slate-700">Properties</span>
        {badgeLabel && (
          <span className="ml-auto badge bg-slate-100 text-slate-500 capitalize">{badgeLabel}</span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {store.selectedEdgeId ? (
          <EdgePanel edgeId={store.selectedEdgeId} />
        ) : !selectedNode || !data ? (
          <div className="flex flex-col items-center justify-center h-32 text-center text-slate-400">
            <Settings size={24} className="mb-2 opacity-30" />
            <p className="text-sm">Select an agent or edge to edit its properties</p>
          </div>
        ) : data.is_inline ? (
          <InlineAgentPanel nodeId={selectedNode.id} data={data} />
        ) : (
          <ExistingAgentPanel nodeId={selectedNode.id} data={data} />
        )}
      </div>
    </div>
  );
}
