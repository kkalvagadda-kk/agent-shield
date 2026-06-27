import { useState } from 'react';
import { toast } from 'sonner';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  addEdge,
  type Connection,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { AgentNode } from '../nodes/AgentNode';
import { EndNode } from '../nodes/EndNode';
import PropertiesPanel from './PropertiesPanel';
import Toolbar from './Toolbar';
import FirstSaveModal from './FirstSaveModal';
import { useWorkflowStore } from '../stores/workflowStore';
import { serializeWorkflow } from '../utils/workflowSerializer';
import { updateWorkflow, deployWorkflow as deployWorkflowApi } from '../api/registryApi';

// ---------------------------------------------------------------------------
// Node type registry — http_tool removed (legacy only, not on canvas)
// ---------------------------------------------------------------------------
const nodeTypes = {
  agent: AgentNode,
  end: EndNode,
};

// ---------------------------------------------------------------------------
// Default configs for new nodes
// ---------------------------------------------------------------------------
function defaultConfigForType(type: 'agent' | 'end') {
  switch (type) {
    case 'agent':
      return {
        name: 'new-agent',
        instructions: '',
        model: 'claude-sonnet-4-6',
        risk: 'low',
        tool_ids: [] as string[],
        skill_ids: [] as string[],
      };
    case 'end':
      return { output_mapping: {} };
  }
}

// ---------------------------------------------------------------------------
// Canvas
// ---------------------------------------------------------------------------
export default function Canvas() {
  const store = useWorkflowStore();
  const [showFirstSaveModal, setShowFirstSaveModal] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);

  // ---- Add node ----
  const addNode = (type: 'agent' | 'end') => {
    const id = crypto.randomUUID();
    const newNode = {
      id,
      type,
      position: {
        x: Math.random() * 400 + 50,
        y: Math.random() * 200 + 50,
      },
      data: { config: defaultConfigForType(type) },
    };
    store.setNodes((prev) => [...prev, newNode]);
  };

  // ---- Save ----
  const handleSave = async () => {
    if (!store.workflowId) {
      // First save — open modal
      setShowFirstSaveModal(true);
      return;
    }
    setIsSaving(true);
    try {
      const definition = serializeWorkflow(store.nodes, store.edges);
      const updated = await updateWorkflow(store.workflowId, { definition });
      store.markSaved(updated.id, updated.name, updated.team);
      toast.success('Workflow saved');
    } catch (err) {
      toast.error(`Save failed: ${String(err)}`);
    } finally {
      setIsSaving(false);
    }
  };

  // ---- Deploy ----
  const handleDeploy = async () => {
    if (!store.workflowId) return;
    setIsDeploying(true);
    try {
      await deployWorkflowApi(store.workflowId);
      toast.success('Deployment started');
    } catch (err) {
      toast.error(`Deploy failed: ${String(err)}`);
    } finally {
      setIsDeploying(false);
    }
  };

  // ---- Connect edges ----
  const onConnect = (connection: Connection) => {
    store.setEdges((edges) =>
      addEdge(
        {
          ...connection,
          type: 'smoothstep',
          animated: true,
          data: { condition: '' },
          label: '',
        },
        edges
      )
    );
  };

  return (
    <div className="flex flex-col h-full">
      <Toolbar
        onAddNode={addNode}
        onSave={handleSave}
        onDeploy={handleDeploy}
        isSaving={isSaving}
        isDeploying={isDeploying}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* React Flow canvas */}
        <div className="flex-1">
          <ReactFlow
            nodes={store.nodes}
            edges={store.edges.map((e) => ({
              ...e,
              style:
                e.id === store.selectedEdgeId
                  ? { stroke: '#3b82f6', strokeWidth: 2.5 }
                  : { stroke: '#94a3b8', strokeWidth: 2 },
            }))}
            onNodesChange={store.onNodesChange}
            onEdgesChange={store.onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => {
              store.selectNode(node.id);
            }}
            onEdgeClick={(_, edge) => {
              store.selectNode(null);
              store.selectEdge(edge.id);
            }}
            onPaneClick={() => {
              store.selectNode(null);
              store.selectEdge(null);
            }}
            nodeTypes={nodeTypes}
            defaultEdgeOptions={{
              type: 'smoothstep',
              animated: true,
              style: { stroke: '#94a3b8', strokeWidth: 2 },
            }}
            edgesFocusable
            fitView
            snapToGrid
            snapGrid={[16, 16]}
          >
            <Background variant={BackgroundVariant.Dots} gap={16} />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>

        {/* Properties panel */}
        <PropertiesPanel />
      </div>

      {/* First-save modal */}
      {showFirstSaveModal && (
        <FirstSaveModal
          onClose={() => setShowFirstSaveModal(false)}
          onSaved={(id, name, team) => {
            store.markSaved(id, name, team);
            setShowFirstSaveModal(false);
          }}
        />
      )}
    </div>
  );
}
