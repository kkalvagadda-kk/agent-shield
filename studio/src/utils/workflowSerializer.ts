import { type Node, type Edge } from '@xyflow/react';

// ---------------------------------------------------------------------------
// Types that map to the backend workflow JSON schema
// ---------------------------------------------------------------------------
export interface WorkflowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  config: Record<string, unknown>;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  condition?: string;
}

export interface WorkflowDefinition {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

// ---------------------------------------------------------------------------
// Serialize: React Flow state → workflow JSON for the API
// ---------------------------------------------------------------------------
export function serializeWorkflow(nodes: Node[], edges: Edge[]): WorkflowDefinition {
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.type ?? 'agent',
      position: { x: node.position.x, y: node.position.y },
      config: ((node.data as { config?: Record<string, unknown> }).config) ?? {},
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      ...(edge.data?.condition ? { condition: String(edge.data.condition) } : {}),
    })),
  };
}

// ---------------------------------------------------------------------------
// Deserialize: workflow JSON from API → React Flow state
// ---------------------------------------------------------------------------
export function deserializeWorkflow(definition: WorkflowDefinition): {
  nodes: Node[];
  edges: Edge[];
} {
  return {
    nodes: definition.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: { x: n.position.x, y: n.position.y },
      data: { config: n.config },
    })),
    edges: definition.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.condition ?? '',
      data: { condition: e.condition ?? '' },
      type: 'smoothstep',
      animated: false,
    })),
  };
}

// ---------------------------------------------------------------------------
// Composite Workflow types (Decision 22 — Agent | Workflow as one executable)
// ---------------------------------------------------------------------------
export interface CompositeWorkflowNode {
  id: string;
  type: 'workflow_member';
  position: { x: number; y: number };
  data: {
    agent_id: string;       // UUID of existing registered agent
    agent_name: string;     // display name (denormalized from registry)
    role?: string;          // 'supervisor' | 'worker' | free-form
    position?: number;      // sequential ordering (1-based)
    is_inline?: boolean;    // true = created inline in this builder (editable here)
    routing?: Record<string, unknown>;  // per-node config (e.g. { max_iterations })
  };
}

export type CompositeOrchestration = 'sequential' | 'conditional' | 'supervisor' | 'handoff';

export interface CompositeWorkflowDefinition {
  orchestration: CompositeOrchestration;
  nodes: CompositeWorkflowNode[];
  edges: WorkflowEdge[];   // reuse existing WorkflowEdge type
}

// ---------------------------------------------------------------------------
// Serialize: React Flow state → composite workflow definition JSON
// ---------------------------------------------------------------------------
export function serializeCompositeWorkflow(
  nodes: Node[],
  edges: Edge[],
  orchestration: CompositeOrchestration,
): CompositeWorkflowDefinition {
  return {
    orchestration,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: 'workflow_member' as const,
      position: { x: node.position.x, y: node.position.y },
      data: {
        agent_id: (node.data as { agent_id: string }).agent_id,
        agent_name: (node.data as { agent_name?: string }).agent_name ?? '',
        role: (node.data as { role?: string }).role,
        position: (node.data as { position?: number }).position,
        is_inline: (node.data as { is_inline?: boolean }).is_inline,
        routing: (node.data as { routing?: Record<string, unknown> }).routing,
      },
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      ...(edge.data?.condition ? { condition: String(edge.data.condition) } : {}),
    })),
  };
}

// ---------------------------------------------------------------------------
// Deserialize: composite workflow definition JSON → React Flow state
// ---------------------------------------------------------------------------
export function deserializeCompositeWorkflow(definition: CompositeWorkflowDefinition): {
  nodes: Node[];
  edges: Edge[];
} {
  return {
    nodes: definition.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: { x: n.position.x, y: n.position.y },
      data: { ...n.data },
    })),
    edges: definition.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.condition ?? '',
      data: { condition: e.condition ?? '' },
      type: 'smoothstep',
      animated: false,
    })),
  };
}
