import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  MarkerType,
  addEdge,
  type Connection,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { toast } from 'sonner';
import {
  ArrowLeft,
  Eye,
  Plus,
  Save,
  Play,
  X,
  Loader2,
  GitMerge,
  Zap,
} from 'lucide-react';
import { WorkflowMemberNode, type WorkflowMemberNodeData } from '../nodes/WorkflowMemberNode';
import AddAgentModal, { type AddedAgent } from '../components/AddAgentModal';
import WorkflowPropertiesPanel from '../components/WorkflowPropertiesPanel';
import WorkflowTriggersPanel from '../components/workflow/WorkflowTriggersPanel';
import TraceDrawer from '../components/playground/TraceDrawer';
import { useWorkflowStore } from '../stores/workflowStore';
import { useAuth } from '../contexts/AuthContext';
import {
  getCompositeWorkflow,
  createCompositeWorkflow,
  updateCompositeWorkflow as updateCompositeWorkflowApi,
  addWorkflowMember,
  removeWorkflowMember,
  addWorkflowEdge,
  listWorkflowEdges,
  removeWorkflowEdge,
  triggerWorkflowRun,
  getWorkflowRunTree,
  type WorkflowRunTree,
  type WorkflowOrchestration,
} from '../api/registryApi';

// ---------------------------------------------------------------------------
// Node types (must be defined outside component to avoid React Flow re-renders)
// ---------------------------------------------------------------------------
const nodeTypes = {
  workflow_member: WorkflowMemberNode,
};

// ---------------------------------------------------------------------------
// Status badge helper
// ---------------------------------------------------------------------------
function statusBadgeCls(status: string): string {
  switch (status) {
    case 'running':
      return 'bg-blue-100 text-blue-700';
    case 'completed':
      return 'bg-green-100 text-green-700';
    case 'failed':
      return 'bg-red-100 text-red-700';
    case 'awaiting_approval':
      return 'bg-amber-100 text-amber-700';
    case 'queued':
    case 'pending':
    default:
      return 'bg-slate-100 text-slate-600';
  }
}

function fmtLatency(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ---------------------------------------------------------------------------
// WorkflowBuilderPage
// ---------------------------------------------------------------------------
export default function WorkflowBuilderPage() {
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const store = useWorkflowStore();
  const { team: authTeam } = useAuth();

  // Team for this workflow (derived from first agent or loaded workflow)
  const [currentTeam, setCurrentTeam] = useState<string>('');

  // Modal / panel visibility
  const [showAddAgent, setShowAddAgent] = useState(false);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [showRunPanel, setShowRunPanel] = useState(false);
  const [showTriggers, setShowTriggers] = useState(false);

  // First-save modal state
  const [saveName, setSaveName] = useState('');
  const [saveOrchestration, setSaveOrchestration] = useState<WorkflowOrchestration>('sequential');
  const [saveShape, setSaveShape] = useState<'reactive' | 'durable'>('reactive');
  const [isSaving, setIsSaving] = useState(false);

  // Run panel state
  const [inputMessage, setInputMessage] = useState('');
  const [isTriggering, setIsTriggering] = useState(false);
  const [runTree, setRunTree] = useState<WorkflowRunTree | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const [viewTraceId, setViewTraceId] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  const pollCountRef = useRef(0);

  // ---------------------------------------------------------------------------
  // Load existing workflow
  // ---------------------------------------------------------------------------
  const {
    data: workflow,
    isLoading,
    refetch: refetchWorkflow,
  } = useQuery({
    queryKey: ['composite-workflow', id],
    queryFn: () => getCompositeWorkflow(id!),
    enabled: !!id,
  });

  useEffect(() => {
    if (!id) {
      store.resetCompositeCanvas();
      return;
    }
    if (workflow) {
      store.markCompositeWorkflowSaved(workflow.id, workflow.name, workflow.team);
      setCurrentTeam(workflow.team);
      setSaveOrchestration(workflow.orchestration);
      const loadedNodes = workflow.members.map((m, idx) => ({
        id: m.agent_id,
        type: 'workflow_member' as const,
        position: { x: (m.position ?? idx + 1) * 240, y: 150 },
        data: {
          agent_id: m.agent_id,
          agent_name: m.agent_name ?? m.agent_id,
          position: m.position ?? idx + 1,
          routing: m.routing ?? {},
          ...(m.role ? { role: m.role } : {}),
        },
      }));
      store.setNodes(loadedNodes);
      // Load persisted edges (node ids are agent ids). Do NOT wipe them.
      const loadedEdges = (workflow.edges ?? []).map((e) => ({
        id: e.id,
        source: e.source_agent_id,
        target: e.target_agent_id,
        label: e.condition ?? '',
        data: { condition: e.condition ?? '' },
        type: 'smoothstep' as const,
        animated: false,
      }));
      store.setEdges(loadedEdges);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow, id]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current !== null) {
        clearInterval(pollRef.current);
      }
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Derived state
  // ---------------------------------------------------------------------------
  const compositeWorkflowId = store.compositeWorkflowId;
  const compositeWorkflowName = store.compositeWorkflowName;

  const alreadyAddedIds = store.nodes
    .map((n) => {
      const d = n.data as WorkflowMemberNodeData;
      return typeof d.agent_id === 'string' ? d.agent_id : '';
    })
    .filter(Boolean);

  // ---------------------------------------------------------------------------
  // Add agent handler
  // ---------------------------------------------------------------------------
  const handleAddAgent = (agent: AddedAgent) => {
    // Cross-team guard
    if (currentTeam && agent.team !== currentTeam) {
      toast.error(
        `Cannot mix teams. This workflow belongs to team "${currentTeam}".`,
      );
      return;
    }
    // Set team from first agent
    if (!currentTeam) {
      setCurrentTeam(agent.team);
    }
    // Skip if already added
    if (alreadyAddedIds.includes(agent.id)) {
      return;
    }
    const pos = store.nodes.length + 1;
    store.setNodes((prev) => [
      ...prev,
      {
        id: agent.id,
        type: 'workflow_member',
        position: { x: pos * 240, y: 150 },
        data: {
          agent_id: agent.id,
          agent_name: agent.name,
          position: pos,
          ...(agent.is_inline ? { is_inline: true } : {}),
        },
      },
    ]);
    // Close the modal after adding so the new node is visible.
    setShowAddAgent(false);
  };

  // ---------------------------------------------------------------------------
  // Save handlers
  // ---------------------------------------------------------------------------
  const handleSave = () => {
    if (store.nodes.length === 0) {
      toast.error('Add at least one agent before saving.');
      return;
    }
    if (!currentTeam) {
      toast.error('Add an agent to determine the workflow team.');
      return;
    }
    if (!compositeWorkflowId) {
      setShowSaveModal(true);
    } else {
      void handleResave();
    }
  };

  const handleFirstSave = async () => {
    if (!saveName.trim()) {
      toast.error('Workflow name is required.');
      return;
    }
    setIsSaving(true);
    try {
      const wf = await createCompositeWorkflow({
        name: saveName.trim(),
        team: currentTeam,
        orchestration: saveOrchestration,
        execution_shape: saveShape,
      });
      for (let i = 0; i < store.nodes.length; i++) {
        const node = store.nodes[i];
        const d = node.data as WorkflowMemberNodeData;
        await addWorkflowMember(wf.id, {
          agent_id: String(d.agent_id),
          position: i + 1,
          ...(d.role ? { role: d.role } : {}),
          ...(d.routing ? { routing: d.routing } : {}),
        });
      }
      await persistEdges(wf.id);
      store.markCompositeWorkflowSaved(wf.id, wf.name, wf.team);
      setShowSaveModal(false);
      setSaveName('');
      toast.success(`Workflow "${wf.name}" saved`);
      navigate(`/workflows/${wf.id}/builder`, { replace: true });
    } catch (err) {
      toast.error(`Save failed: ${String(err)}`);
    } finally {
      setIsSaving(false);
    }
  };

  // Persist the current canvas edges to the workflow (node ids are agent ids).
  const persistEdges = async (workflowId: string) => {
    for (let i = 0; i < store.edges.length; i++) {
      const edge = store.edges[i];
      const cond = (edge.data as { condition?: string } | undefined)?.condition;
      await addWorkflowEdge(workflowId, {
        source_agent_id: edge.source,
        target_agent_id: edge.target,
        condition: cond && cond.trim() ? cond : null,
        position: i + 1,
      });
    }
  };

  const handleResave = async () => {
    if (!compositeWorkflowId) return;
    setIsSaving(true);
    try {
      // Keep orchestration mode in sync with the toolbar selection.
      if (workflow && workflow.orchestration !== saveOrchestration) {
        await updateCompositeWorkflowApi(compositeWorkflowId, { orchestration: saveOrchestration });
      }
      // Replace members: remove existing, re-add current canvas nodes (with role/routing).
      for (const m of workflow?.members ?? []) {
        await removeWorkflowMember(compositeWorkflowId, m.agent_id);
      }
      for (let i = 0; i < store.nodes.length; i++) {
        const node = store.nodes[i];
        const d = node.data as WorkflowMemberNodeData;
        await addWorkflowMember(compositeWorkflowId, {
          agent_id: String(d.agent_id),
          position: i + 1,
          ...(d.role ? { role: d.role } : {}),
          ...(d.routing ? { routing: d.routing } : {}),
        });
      }
      // Replace edges: remove existing, re-add current canvas edges.
      for (const e of await listWorkflowEdges(compositeWorkflowId)) {
        await removeWorkflowEdge(compositeWorkflowId, e.id);
      }
      await persistEdges(compositeWorkflowId);
      toast.success('Workflow saved');
      void refetchWorkflow();
    } catch (err) {
      toast.error(`Save failed: ${String(err)}`);
    } finally {
      setIsSaving(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Run handlers
  // ---------------------------------------------------------------------------
  const stopPolling = () => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setIsPolling(false);
  };

  const handleTriggerRun = async () => {
    if (!compositeWorkflowId) return;
    setIsTriggering(true);
    setRunTree(null);
    try {
      const result = await triggerWorkflowRun(compositeWorkflowId, {
        input_payload: { message: inputMessage },
        run_by: 'studio-user',
      });
      if (result.warning) toast.warning(result.warning);
      setIsPolling(true);
      pollCountRef.current = 0;
      pollRef.current = window.setInterval(async () => {
        pollCountRef.current++;
        try {
          const tree = await getWorkflowRunTree(
            compositeWorkflowId,
            result.run_id,
          );
          setRunTree(tree);
          const done =
            tree.parent.status === 'completed' ||
            tree.parent.status === 'failed' ||
            tree.parent.status === 'cancelled';
          if (done || pollCountRef.current >= 90) {
            stopPolling();
          }
        } catch {
          if (pollCountRef.current >= 90) {
            stopPolling();
          }
        }
      }, 3000);
    } catch (err) {
      toast.error(`Failed to trigger run: ${String(err)}`);
    } finally {
      setIsTriggering(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Edge connection
  // ---------------------------------------------------------------------------
  const onConnect = (connection: Connection) => {
    store.setEdges((edges) =>
      addEdge(
        {
          ...connection,
          type: 'smoothstep',
          animated: false,
          data: { condition: '' },
          label: '',
        },
        edges,
      ),
    );
  };

  // ---------------------------------------------------------------------------
  // Loading state
  // ---------------------------------------------------------------------------
  if (id && isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" />
        Loading workflow…
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div className="flex flex-col h-screen">
      {/* ---- Toolbar ---- */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-slate-200 bg-white shrink-0">
        <button
          onClick={() => navigate('/workflows')}
          className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 transition-colors"
        >
          <ArrowLeft size={14} />
          Workflows
        </button>
        <span className="text-slate-300">/</span>
        <span className="text-sm font-medium text-slate-700">
          {compositeWorkflowName ?? 'New Workflow'}
        </span>
        {currentTeam && (
          <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
            {currentTeam}
          </span>
        )}

        <div className="flex-1" />

        {/* Run button (only when saved) */}
        {compositeWorkflowId && (
          <button
            onClick={() => {
              setShowRunPanel((v) => !v);
              if (!showRunPanel) {
                setRunTree(null);
                setInputMessage('');
              }
            }}
            className="btn-secondary flex items-center gap-1.5 text-sm"
          >
            <Play size={13} />
            Run Workflow
          </button>
        )}

        {/* Triggers button (only when saved) */}
        {compositeWorkflowId && (
          <button
            onClick={() => setShowTriggers(true)}
            className="btn-secondary flex items-center gap-1.5 text-sm"
          >
            <Zap size={13} />
            Triggers
          </button>
        )}

        {/* Save button */}
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="btn-primary flex items-center gap-1.5 text-sm"
        >
          {isSaving ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Save size={13} />
          )}
          {isSaving ? 'Saving…' : 'Save'}
        </button>

        {/* Add agent button — opens a modal with Existing + Create New tabs */}
        <button
          onClick={() => setShowAddAgent(true)}
          className="btn-primary flex items-center gap-1.5 text-sm"
        >
          <Plus size={13} />
          Add Agent
        </button>
      </div>

      {/* ---- Main area (canvas + optional run panel) ---- */}
      <div className="flex flex-1 overflow-hidden">
        {/* Canvas */}
        <div className="flex-1 relative">
          {store.nodes.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-center pointer-events-none z-10">
              <GitMerge size={40} className="text-slate-200 mb-3" />
              <p className="text-slate-400 font-medium text-sm">
                Add agents to build your workflow
              </p>
              <p className="text-slate-300 text-xs mt-1 max-w-xs">
                Click "Add Agent" to pick an existing agent or create a new one.
              </p>
            </div>
          )}
          <ReactFlow
            nodes={store.nodes}
            edges={store.edges.map((e) => ({
              ...e,
              animated: false,
              style: { stroke: '#94a3b8', strokeWidth: 2 },
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: '#94a3b8',
              },
            }))}
            onNodesChange={store.onNodesChange}
            onEdgesChange={store.onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => { store.selectNode(node.id); }}
            onEdgeClick={(_, edge) => { store.selectEdge(edge.id); }}
            onPaneClick={() => { store.selectNode(null); store.selectEdge(null); }}
            nodeTypes={nodeTypes}
            defaultEdgeOptions={{
              type: 'smoothstep',
              style: { stroke: '#94a3b8', strokeWidth: 2 },
              markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8' },
            }}
            fitView
            snapToGrid
            snapGrid={[16, 16]}
          >
            <Background variant={BackgroundVariant.Dots} gap={16} />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>

        {/* ---- Properties panel (node / edge config) ---- */}
        <WorkflowPropertiesPanel />

        {/* ---- Run panel (right side) ---- */}
        {showRunPanel && (
          <div className="w-96 border-l border-slate-200 bg-white flex flex-col shrink-0 overflow-hidden">
            {/* Panel header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
              <h3 className="text-sm font-semibold text-slate-800 flex items-center gap-2">
                <Play size={14} className="text-blue-500" />
                Run Workflow
              </h3>
              <button
                onClick={() => {
                  setShowRunPanel(false);
                  stopPolling();
                }}
                className="text-slate-400 hover:text-slate-600 transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            {/* Input area */}
            {!runTree && (
              <div className="p-4 space-y-3">
                <label className="label text-xs">Input message</label>
                <textarea
                  className="input resize-none text-sm"
                  rows={4}
                  value={inputMessage}
                  onChange={(e) => setInputMessage(e.target.value)}
                  placeholder="Enter the message to pass to the first agent…"
                />
                <button
                  onClick={() => void handleTriggerRun()}
                  disabled={isTriggering || !inputMessage.trim()}
                  className="btn-primary w-full flex items-center justify-center gap-2 text-sm"
                >
                  {isTriggering ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Play size={14} />
                  )}
                  {isTriggering ? 'Starting…' : 'Start Run'}
                </button>
              </div>
            )}

            {/* Run tree display */}
            {runTree && (
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {/* Parent run status */}
                <div className="rounded-lg border border-slate-200 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-semibold text-slate-700 uppercase tracking-wider">
                      Workflow Run
                    </span>
                    <span
                      className={`text-xs font-medium px-2 py-0.5 rounded-full capitalize ${statusBadgeCls(runTree.parent.status)}`}
                    >
                      {runTree.parent.status}
                    </span>
                  </div>
                  <p className="text-xs text-slate-500">
                    {runTree.parent.agent_name}
                  </p>
                  {isPolling && (
                    <div className="flex items-center gap-1.5 mt-2 text-xs text-blue-500">
                      <Loader2 size={12} className="animate-spin" />
                      Polling for updates…
                    </div>
                  )}
                  {runTree.parent.output && !isPolling && (
                    <div className="mt-3">
                      <p className="text-[10px] font-semibold text-slate-500 uppercase mb-1">Final Output</p>
                      <pre className="text-xs text-slate-700 bg-white border border-slate-200 rounded p-2 whitespace-pre-wrap max-h-60 overflow-y-auto">
                        {runTree.parent.output}
                      </pre>
                    </div>
                  )}
                  {runTree.parent.error_message && (
                    <p className="mt-2 text-xs text-red-600">{runTree.parent.error_message}</p>
                  )}
                  {runTree.parent.langfuse_trace_id && (
                    <button
                      onClick={() => setViewTraceId(runTree.parent.langfuse_trace_id)}
                      className="inline-flex items-center gap-1 mt-2 text-xs text-blue-600 hover:text-blue-800 font-medium"
                    >
                      <Eye size={12} />
                      View Trace
                    </button>
                  )}
                </div>

                {/* Child runs */}
                {runTree.children.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                      Agent Steps
                    </p>
                    <div className="space-y-2">
                      {runTree.children.map((child, idx) => (
                        <div
                          key={child.id}
                          className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2"
                        >
                          <div className="flex items-center gap-3">
                            <span className="text-xs font-bold text-slate-400 w-4 shrink-0">
                              {idx + 1}
                            </span>
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium text-slate-700 truncate">
                                {child.agent_name}
                              </p>
                              <p className="text-[10px] text-slate-400">
                                {fmtLatency(child.latency_ms)}
                              </p>
                            </div>
                            <span
                              className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full capitalize shrink-0 ${statusBadgeCls(child.status)}`}
                            >
                              {child.status}
                            </span>
                          </div>
                          {child.output && (
                            <pre className="mt-2 text-xs text-slate-600 bg-white border border-slate-200 rounded p-2 whitespace-pre-wrap max-h-40 overflow-y-auto">
                              {child.output}
                            </pre>
                          )}
                          {child.error_message && (
                            <p className="mt-1 text-xs text-red-600">{child.error_message}</p>
                          )}
                          {child.langfuse_trace_id && (
                            <button
                              onClick={() => setViewTraceId(child.langfuse_trace_id)}
                              className="inline-flex items-center gap-1 mt-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                            >
                              <Eye size={12} />
                              View Trace
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {runTree.children.length === 0 && !isPolling && (
                  <p className="text-xs text-slate-400 text-center py-2">
                    No child runs yet.
                  </p>
                )}

                {/* New run button */}
                <button
                  onClick={() => {
                    setRunTree(null);
                    setInputMessage('');
                    stopPolling();
                  }}
                  className="btn-secondary w-full text-sm"
                >
                  Run Again
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ---- Add Agent Modal ---- */}
      {showAddAgent && (
        <AddAgentModal
          team={currentTeam || authTeam || ''}
          onAdd={handleAddAgent}
          onClose={() => setShowAddAgent(false)}
          alreadyAddedIds={alreadyAddedIds}
        />
      )}

      {/* ---- Workflow Triggers Panel ---- */}
      {showTriggers && compositeWorkflowId && (
        <WorkflowTriggersPanel
          workflowId={compositeWorkflowId}
          workflowName={compositeWorkflowName ?? 'workflow'}
          onClose={() => setShowTriggers(false)}
        />
      )}

      {/* ---- First-Save Modal ---- */}
      {showSaveModal && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={(e) => e.target === e.currentTarget && setShowSaveModal(false)}
        >
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold text-slate-900">
                Save Workflow
              </h2>
              <button
                onClick={() => setShowSaveModal(false)}
                className="text-slate-400 hover:text-slate-600 transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-4">
              {/* Name */}
              <div>
                <label className="label" htmlFor="wfb-name">
                  Workflow Name{' '}
                  <span className="text-red-500">*</span>
                </label>
                <input
                  id="wfb-name"
                  className="input"
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder="my-workflow"
                  autoFocus
                />
              </div>

              {/* Team (read-only, derived from agents) */}
              <div>
                <label className="label">Team</label>
                <div className="input bg-slate-50 text-slate-500 cursor-not-allowed">
                  {currentTeam}
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  Derived from the agents in this workflow.
                </p>
              </div>

              {/* Execution shape */}
              <div>
                <label className="label">Execution Shape</label>
                <select
                  className="input"
                  value={saveShape}
                  onChange={(e) => setSaveShape(e.target.value as 'reactive' | 'durable')}
                >
                  <option value="reactive">Reactive (fast, stateless request/response)</option>
                  <option value="durable">Durable (long-running, resumable, HITL)</option>
                </select>
              </div>

              {/* Orchestration mode */}
              <div>
                <label className="label" htmlFor="wfb-orch">
                  Orchestration Mode
                </label>
                <select
                  id="wfb-orch"
                  className="input"
                  value={saveOrchestration}
                  onChange={(e) => setSaveOrchestration(e.target.value as WorkflowOrchestration)}
                >
                  <option value="sequential">Sequential</option>
                  <option value="conditional">Conditional (edge conditions route)</option>
                  <option value="supervisor">Supervisor (a coordinator routes)</option>
                  <option value="handoff">Handoff (agents pass control)</option>
                </select>
                <p className="mt-1 text-xs text-slate-400">
                  Sequential follows the edge chain. Conditional routes on edge conditions.
                  Supervisor needs a member with role “supervisor”. Handoff follows each agent’s
                  handoff signal.
                </p>
              </div>

              {/* Actions */}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowSaveModal(false)}
                  className="btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void handleFirstSave()}
                  disabled={isSaving || !saveName.trim()}
                  className="btn-primary"
                >
                  {isSaving ? 'Saving…' : 'Save Workflow'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {viewTraceId && (
        <TraceDrawer traceId={viewTraceId} onClose={() => setViewTraceId(null)} />
      )}
    </div>
  );
}
