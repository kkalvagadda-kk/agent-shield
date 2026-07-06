import { create } from 'zustand';
import {
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  applyNodeChanges,
  applyEdgeChanges,
} from '@xyflow/react';

// ---------------------------------------------------------------------------
// NodeConfig — loose enough to cover all node types
// ---------------------------------------------------------------------------
export type NodeConfig = Record<string, unknown>;

// ---------------------------------------------------------------------------
// State + actions
// ---------------------------------------------------------------------------
interface WorkflowState {
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  isDirty: boolean;
  workflowId: string | null;
  workflowName: string | null;
  team: string | null;

  // Composite workflow (Decision 22) — separate from the agent-graph canvas above
  compositeWorkflowId: string | null;
  compositeWorkflowName: string | null;

  // Actions
  setNodes: (nodes: Node[] | ((nodes: Node[]) => Node[])) => void;
  setEdges: (edges: Edge[] | ((edges: Edge[]) => Edge[])) => void;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  selectNode: (id: string | null) => void;
  selectEdge: (id: string | null) => void;
  updateNodeConfig: (id: string, config: Partial<NodeConfig>) => void;
  updateNodeData: (id: string, data: Record<string, unknown>) => void;
  updateEdgeCondition: (id: string, condition: string) => void;
  markSaved: (workflowId: string, name: string, team: string) => void;
  resetCanvas: () => void;
  markCompositeWorkflowSaved: (id: string, name: string, team: string) => void;
  resetCompositeCanvas: () => void;
}

export const useWorkflowStore = create<WorkflowState>()((set, get) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  selectedEdgeId: null,
  isDirty: false,
  workflowId: null,
  workflowName: null,
  team: null,
  compositeWorkflowId: null,
  compositeWorkflowName: null,

  setNodes: (nodes) => {
    set((state) => ({
      nodes: typeof nodes === 'function' ? nodes(state.nodes) : nodes,
      isDirty: true,
    }));
  },

  setEdges: (edges) => {
    set((state) => ({
      edges: typeof edges === 'function' ? edges(state.edges) : edges,
      isDirty: true,
    }));
  },

  onNodesChange: (changes) => {
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes),
      isDirty: true,
    }));
  },

  onEdgesChange: (changes) => {
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
      isDirty: true,
    }));
  },

  selectNode: (id) => {
    set({ selectedNodeId: id, selectedEdgeId: null });
  },

  selectEdge: (id) => {
    set({ selectedEdgeId: id, selectedNodeId: null });
  },

  updateEdgeCondition: (id, condition) => {
    set((state) => ({
      edges: state.edges.map((edge) =>
        edge.id === id
          ? {
              ...edge,
              label: condition,
              data: { ...(edge.data ?? {}), condition },
            }
          : edge
      ),
      isDirty: true,
    }));
  },

  updateNodeConfig: (id, config) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === id
          ? {
              ...node,
              data: {
                ...node.data,
                config: {
                  ...((node.data as { config?: NodeConfig }).config ?? {}),
                  ...config,
                },
              },
            }
          : node
      ),
      isDirty: true,
    }));
  },

  updateNodeData: (id, data) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === id ? { ...node, data: { ...node.data, ...data } } : node
      ),
      isDirty: true,
    }));
  },

  markSaved: (workflowId, name, team) => {
    set({ workflowId, workflowName: name, team, isDirty: false });
  },

  resetCanvas: () => {
    set({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      selectedEdgeId: null,
      isDirty: false,
      workflowId: null,
      workflowName: null,
      team: null,
    });
  },

  markCompositeWorkflowSaved: (id, name, team) => {
    set({ compositeWorkflowId: id, compositeWorkflowName: name, team, isDirty: false });
  },

  resetCompositeCanvas: () => {
    set({
      nodes: [],
      edges: [],
      selectedNodeId: null,
      selectedEdgeId: null,
      isDirty: false,
      compositeWorkflowId: null,
      compositeWorkflowName: null,
    });
  },
}));
