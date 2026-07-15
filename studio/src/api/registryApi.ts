import axios from "axios";
import { getKeycloak } from "../lib/keycloak";

export const http = axios.create({ baseURL: "/api/v1" });

// Attach Bearer token on every request; refresh if stale first
http.interceptors.request.use(async (config) => {
  const kc = getKeycloak();
  if (kc?.authenticated) {
    try {
      await kc.updateToken(10);
    } catch {
      kc.logout();
      return Promise.reject(new Error("Session expired"));
    }
    config.headers.Authorization = `Bearer ${kc.token}`;
  }
  return config;
});

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface Agent {
  id: string;
  name: string;
  team: string;
  description: string | null;
  status: string;
  agent_type: string;
  publish_status: string;
  agent_class: string | null;
  execution_shape: "reactive" | "durable";
  memory_enabled: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
  metadata: Record<string, unknown>;
  latest_version_number: number | null;
}

export interface AgentVersion {
  id: string;
  agent_id: string;
  version_number: number;
  image_tag: string | null;
  agent_graph_id: string | null;
  tools: { name: string; risk: string; description?: string }[];
  eval_passed: boolean;
  adversarial_eval_passed: boolean;
  git_sha: string | null;
  git_branch: string | null;
  notes: string | null;
  status: string;
  created_at: string;
  created_by: string | null;
}

export interface Deployment {
  id: string;
  agent_id: string;
  agent_name: string | null;
  version_id: string;
  environment: string;
  status: string;
  replicas: number;
  canary_percent: number | null;
  k8s_namespace: string;
  k8s_deployment_name: string | null;
  error_message: string | null;
  deployed_at: string;
  terminated_at: string | null;
  deployed_by: string | null;
  previous_version_id: string | null;
  name: string | null;
  suspended_at: string | null;
  ttl_hours: number | null;
}

export type DeploymentAction = "suspend" | "resume" | "terminate" | "upgrade";

export interface LLMProvider {
  id: string;
  name: string;
  provider: "anthropic" | "bedrock";
  default_model: string;
  team: string;
  created_at: string;
  updated_at: string;
}

export interface LLMProviderCreate {
  name: string;
  provider: "anthropic" | "bedrock";
  default_model: string;
  team: string;
  credentials: Record<string, string>;
}

export interface Team {
  id: string;
  name: string;
  namespace: string;
  description: string | null;
  keycloak_role_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
}

export interface RegistryTool {
  id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  type: string;
  risk_level?: 'low' | 'medium' | 'high';
  owner_team?: string;
  status?: string;
  auth_config_id?: string | null;
  // HTTP tool fields (top-level in ToolResponse)
  http_method?: string;
  http_url?: string;
  // Header templates like {"X-API-KEY": "{{serper_api_key}}"}. The {{...}}
  // placeholders name the env vars the tool reads at runtime — see
  // sdk/agentshield_sdk/tool_executor.py, which substitutes header placeholders
  // from os.environ. Those env vars are injected from the credential Secret via
  // the pod's envFrom, so each placeholder name IS the exact credential key the
  // tool expects. Read-only here; surfaced so the UI can drive the key name.
  http_headers?: Record<string, string> | null;
  // Python tool fields
  python_code?: string;
  config: Record<string, unknown>;
}

/**
 * Valid Kubernetes/POSIX environment-variable name: a letter or underscore
 * followed by letters, digits, or underscores. Hyphenated names (e.g.
 * "serper-dev") are INVALID env vars and are silently dropped by K8s `envFrom`,
 * so a credential keyed that way never reaches the agent.
 */
export const ENV_VAR_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

export const isValidEnvVarName = (key: string): boolean =>
  ENV_VAR_NAME_RE.test(key);

/**
 * The credential keys a tool expects, derived from the {{...}} placeholders in
 * its HTTP header templates. This mirrors the runtime contract in
 * tool_executor.py (header placeholders are resolved from the pod env). A tool
 * with no credential-bearing headers returns []. Names are de-duplicated and
 * returned in first-seen order.
 */
export const expectedCredentialKeys = (tool: RegistryTool): string[] => {
  const headers = tool.http_headers;
  if (!headers) return [];
  const seen = new Set<string>();
  const keys: string[] = [];
  for (const value of Object.values(headers)) {
    if (typeof value !== 'string') continue;
    for (const m of value.matchAll(/\{\{(\w+)\}\}/g)) {
      const name = m[1];
      if (!seen.has(name)) {
        seen.add(name);
        keys.push(name);
      }
    }
  }
  return keys;
};

export interface Skill {
  id: string;
  name: string;
  team: string;
  description: string | null;
  tool_ids: string[];
  status: string;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------
export const listAgents = async (
  limit = 100,
  offset = 0,
  status?: string,
  opts?: { composable?: boolean }
): Promise<Paginated<Agent>> => {
  const { data } = await http.get<Paginated<Agent>>("/agents/", {
    params: {
      limit,
      offset,
      ...(status !== undefined ? { status } : {}),
      ...(opts?.composable ? { composable: true } : {}),
    },
  });
  return data;
};

export const getAgent = async (name: string): Promise<Agent> => {
  const { data } = await http.get<Agent>(`/agents/${name}`);
  return data;
};

export const createAgent = async (body: {
  name: string;
  team: string;
  description?: string;
  agent_type?: string;
  agent_class?: "user_delegated" | "daemon";
  execution_shape?: "reactive" | "durable";
  memory_enabled?: boolean;
  metadata?: Record<string, unknown>;
  tools?: string[];
}): Promise<Agent> => {
  const { data } = await http.post<Agent>("/agents/", body);
  return data;
};

export const updateAgent = async (
  name: string,
  body: {
    description?: string;
    status?: string;
    execution_shape?: "reactive" | "durable";
    agent_class?: "user_delegated" | "daemon";
    memory_enabled?: boolean;
    metadata?: Record<string, unknown>;
  }
): Promise<Agent> => {
  const { data } = await http.put<Agent>(`/agents/${name}`, body);
  return data;
};

export const deleteAgent = async (name: string): Promise<void> => {
  await http.delete(`/agents/${name}`);
};

// ---------------------------------------------------------------------------
// Versions
// ---------------------------------------------------------------------------
export const listVersions = async (
  agentName: string
): Promise<AgentVersion[]> => {
  const { data } = await http.get<AgentVersion[]>(
    `/agents/${agentName}/versions`
  );
  return data;
};

export const createVersion = async (
  agentName: string,
  body: { image_tag?: string; eval_passed?: boolean; notes?: string }
): Promise<AgentVersion> => {
  const { data } = await http.post<AgentVersion>(
    `/agents/${agentName}/versions`,
    body
  );
  return data;
};

export const patchVersion = async (
  agentName: string,
  versionId: string,
  body: { eval_passed?: boolean; adversarial_eval_passed?: boolean; notes?: string }
): Promise<AgentVersion> => {
  const { data } = await http.patch<AgentVersion>(
    `/agents/${agentName}/versions/${versionId}`,
    body
  );
  return data;
};

export const deleteAgentVersion = async (
  agentName: string,
  versionId: string
): Promise<{ deleted_version_id: string; terminated_deployments: number }> => {
  const { data } = await http.delete(`/agents/${agentName}/versions/${versionId}`);
  return data;
};

// ---------------------------------------------------------------------------
// Deployments
// ---------------------------------------------------------------------------
export const deployAgent = async (
  agentName: string,
  body: { version_id?: string; replicas?: number; environment?: string; name?: string; ttl_hours?: number }
): Promise<Deployment> => {
  const { data } = await http.post<Deployment>(
    `/agents/${agentName}/deploy`,
    body
  );
  return data;
};

// Lifecycle action on a sandbox deployment (suspend/resume/terminate/upgrade).
// "Upgrade" is how a deployment's settings change — point it at a new version.
export const updateSandboxDeployment = async (
  agentName: string,
  deploymentId: string,
  action: DeploymentAction,
  versionId?: string
): Promise<Deployment> => {
  const { data } = await http.patch<Deployment>(
    `/agents/${agentName}/deployments/${deploymentId}`,
    { action, version_id: versionId }
  );
  return data;
};

export const rollbackAgent = async (
  agentName: string
): Promise<Deployment> => {
  const { data } = await http.post<Deployment>(
    `/agents/${agentName}/rollback`
  );
  return data;
};

export const getDeployments = async (
  agentName: string
): Promise<Deployment[]> => {
  const { data } = await http.get<Deployment[]>(
    `/agents/${agentName}/deployments`
  );
  return data;
};

// ---------------------------------------------------------------------------
// Deployment-scoped stats + runs (metrics belong to a deployment, not the
// artifact). `context` selects the run-isolation column on the backend.
// ---------------------------------------------------------------------------
export type DeploymentContext = "playground" | "production";

export const getDeploymentStats = async (
  deploymentId: string,
  context: DeploymentContext
): Promise<AgentStats> => {
  const { data } = await http.get<AgentStats>(
    `/deployments/${deploymentId}/stats`,
    { params: { context } }
  );
  return data;
};

export const listDeploymentRuns = async (
  deploymentId: string,
  params: {
    context: DeploymentContext;
    trigger_type?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }
): Promise<AgentRunItem[]> => {
  const { data } = await http.get<AgentRunItem[]>(
    `/deployments/${deploymentId}/runs`,
    { params }
  );
  return data;
};

export interface AgentChatStart {
  run_id: string;
  session_id: string;
  stream_url: string;
  agent_name: string;
  deployment_id: string;
}

export const startAgentChat = async (
  name: string,
  body: {
    message: string;
    session_id?: string;
    context?: "production" | "playground";
    // Pin the run to an exact deployment (prod: ProductionDeployment id). When
    // omitted the backend resolves the single running deployment for the context.
    deployment_id?: string;
  }
): Promise<AgentChatStart> => {
  const { data } = await http.post<AgentChatStart>(`/agents/${name}/chat`, body);
  return data;
};

export const startDeploymentChat = async (
  name: string,
  depId: string,
  body: { message: string; session_id?: string }
): Promise<AgentChatStart> => {
  const { data } = await http.post<AgentChatStart>(
    `/agents/${name}/deployments/${depId}/chat`,
    body
  );
  return data;
};

export const listAllDeployments = async (
  status?: string,
  limit = 100,
  environment?: string
): Promise<Paginated<Deployment>> => {
  const { data } = await http.get<Paginated<Deployment>>("/deployments/", {
    params: { limit, ...(status ? { status } : {}), ...(environment ? { environment } : {}) },
  });
  return data;
};

export const listAllWorkflowDeployments = async (
  status?: string,
  environment?: string,
  limit = 100
): Promise<WorkflowDeployment[]> => {
  const { data } = await http.get<WorkflowDeployment[]>("/deployments/workflows", {
    params: { limit, ...(status ? { status } : {}), ...(environment ? { environment } : {}) },
  });
  return data;
};

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------
export const listTeams = async (): Promise<Paginated<Team>> => {
  const { data } = await http.get<Paginated<Team>>("/teams/");
  return data;
};

// ---------------------------------------------------------------------------
// LLM Providers
// ---------------------------------------------------------------------------
export const listProviders = async (team?: string): Promise<Paginated<LLMProvider>> => {
  const { data } = await http.get<Paginated<LLMProvider>>("/llm-providers/", {
    params: team ? { team } : undefined,
  });
  return data;
};

export const createProvider = async (body: LLMProviderCreate): Promise<LLMProvider> => {
  const { data } = await http.post<LLMProvider>("/llm-providers/", body);
  return data;
};

export const updateProvider = async (
  id: string,
  body: Partial<LLMProviderCreate>
): Promise<LLMProvider> => {
  const { data } = await http.put<LLMProvider>(`/llm-providers/${id}`, body);
  return data;
};

export const deleteProvider = async (id: string): Promise<void> => {
  await http.delete(`/llm-providers/${id}`);
};

// ---------------------------------------------------------------------------
// Agent Graphs (renamed from Workflows — canvas-based single-agent graphs)
// ---------------------------------------------------------------------------
export interface AgentGraphVersion {
  id: string;
  agent_graph_id: string;
  version_number: number;
  definition: { nodes: unknown[]; edges: unknown[] };
  change_summary: string | null;
  created_at: string;
}

export interface AgentGraph {
  id: string;
  name: string;
  team: string;
  description: string | null;
  status: string;
  current_version_number?: number | null;
  current_definition?: AgentGraphVersion | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowDefinition {
  nodes: unknown[];
  edges: unknown[];
}

export const createAgentGraph = async (body: {
  name: string;
  team: string;
  description?: string;
  definition: WorkflowDefinition;
}): Promise<AgentGraph> => {
  const { data } = await http.post<AgentGraph>("/agent-graphs/", body);
  return data;
};

export const updateAgentGraph = async (
  id: string,
  body: { definition: WorkflowDefinition; change_summary?: string }
): Promise<AgentGraph> => {
  const { data } = await http.put<AgentGraph>(`/agent-graphs/${id}`, body);
  return data;
};

export const deployAgentGraph = async (
  id: string,
  body?: { replicas?: number }
): Promise<unknown> => {
  const { data } = await http.post(`/agent-graphs/${id}/deploy`, body ?? {});
  return data;
};

export const listAgentGraphs = async (params?: { team?: string }): Promise<AgentGraph[]> => {
  const { data } = await http.get<AgentGraph[]>('/agent-graphs/', { params });
  return data;
};

export const getAgentGraph = async (id: string): Promise<AgentGraph> => {
  const { data } = await http.get<AgentGraph>(`/agent-graphs/${id}`);
  return data;
};

// ---------------------------------------------------------------------------
// Composite Workflows (Decision 22 — new /api/v1/workflows endpoint)
// ---------------------------------------------------------------------------
export interface CompositeWorkflow {
  id: string;
  name: string;
  team: string;
  description: string | null;
  execution_shape: 'reactive' | 'durable';
  orchestration: WorkflowOrchestration;
  agent_class: 'user_delegated' | 'daemon';
  memory_enabled: boolean;
  status: 'draft' | 'published' | 'archived';
  publish_status: string;
  member_count: number;
  warnings?: string[];
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export type WorkflowOrchestration = 'sequential' | 'conditional' | 'supervisor' | 'handoff';

export interface WorkflowMember {
  workflow_id: string;
  agent_id: string;
  agent_name: string | null;
  role: string | null;
  position: number | null;
  routing: Record<string, unknown>;
  added_at: string;
}

export interface WorkflowEdge {
  id: string;
  workflow_id: string;
  source_agent_id: string;
  target_agent_id: string;
  condition: string | null;
  position: number | null;
  created_at: string;
}

export interface CompositeWorkflowWithMembers extends CompositeWorkflow {
  members: WorkflowMember[];
  edges: WorkflowEdge[];
}

export interface CreateCompositeWorkflowRequest {
  name: string;
  team: string;
  description?: string;
  execution_shape?: 'reactive' | 'durable';
  orchestration?: WorkflowOrchestration;
  agent_class?: 'user_delegated' | 'daemon';
  memory_enabled?: boolean;
}

export interface WorkflowRunResult {
  run_id: string;
  workflow_id: string;
  status: string;
  started_at: string;
  warning?: string | null;
}

export interface WorkflowRunTree {
  parent: AgentRunItem;
  children: AgentRunItem[];
}

export const listCompositeWorkflows = async (params?: { team?: string }): Promise<CompositeWorkflow[]> => {
  const { data } = await http.get<CompositeWorkflow[]>('/workflows', { params });
  return data;
};

export const createCompositeWorkflow = async (
  body: CreateCompositeWorkflowRequest,
): Promise<CompositeWorkflow> => {
  const { data } = await http.post<CompositeWorkflow>('/workflows', body);
  return data;
};

export const getCompositeWorkflow = async (id: string): Promise<CompositeWorkflowWithMembers> => {
  const { data } = await http.get<CompositeWorkflowWithMembers>(`/workflows/${id}`);
  return data;
};

export const updateCompositeWorkflow = async (
  id: string,
  body: Partial<CreateCompositeWorkflowRequest> & { status?: string },
): Promise<CompositeWorkflow> => {
  const { data } = await http.patch<CompositeWorkflow>(`/workflows/${id}`, body);
  return data;
};

export const deleteCompositeWorkflow = async (id: string): Promise<void> => {
  await http.delete(`/workflows/${id}`);
};

export const publishWorkflow = async (
  id: string,
  versionId?: string
): Promise<{ publish_request_id: string }> => {
  const { data } = await http.post<{ publish_request_id: string }>(
    `/workflows/${id}/publish`,
    versionId ? { version_id: versionId } : {}
  );
  return data;
};

export const addWorkflowMember = async (
  workflowId: string,
  body: { agent_id: string; role?: string; position?: number; routing?: Record<string, unknown> },
): Promise<WorkflowMember> => {
  const { data } = await http.post<WorkflowMember>(`/workflows/${workflowId}/members`, body);
  return data;
};

export const removeWorkflowMember = async (
  workflowId: string,
  agentId: string,
): Promise<void> => {
  await http.delete(`/workflows/${workflowId}/members/${agentId}`);
};

export const addWorkflowEdge = async (
  workflowId: string,
  body: { source_agent_id: string; target_agent_id: string; condition?: string | null; position?: number },
): Promise<WorkflowEdge> => {
  const { data } = await http.post<WorkflowEdge>(`/workflows/${workflowId}/edges`, body);
  return data;
};

export const listWorkflowEdges = async (workflowId: string): Promise<WorkflowEdge[]> => {
  const { data } = await http.get<WorkflowEdge[]>(`/workflows/${workflowId}/edges`);
  return data;
};

export const removeWorkflowEdge = async (workflowId: string, edgeId: string): Promise<void> => {
  await http.delete(`/workflows/${workflowId}/edges/${edgeId}`);
};

// ---------------------------------------------------------------------------
// Workflow-level triggers (schedule / webhook) — fired by scheduler / event-gateway
// ---------------------------------------------------------------------------
export const listWorkflowTriggers = async (
  workflowId: string,
): Promise<AgentTrigger[]> => {
  const { data } = await http.get<AgentTrigger[]>(`/workflows/${workflowId}/triggers`);
  return data;
};

export const createWorkflowTrigger = async (
  workflowId: string,
  body: {
    trigger_type: 'schedule' | 'webhook';
    cron_expression?: string;
    timezone?: string;
    enabled?: boolean;
    filter_conditions?: Record<string, unknown> | Record<string, unknown>[];
    input_payload?: Record<string, unknown> | null;
    alert_email?: string | null;
    alert_on_failure?: boolean;
  },
): Promise<AgentTrigger> => {
  const { data } = await http.post<AgentTrigger>(`/workflows/${workflowId}/triggers`, body);
  return data;
};

export const updateWorkflowTrigger = async (
  workflowId: string,
  triggerId: string,
  body: {
    cron_expression?: string;
    timezone?: string;
    enabled?: boolean;
    filter_conditions?: Record<string, unknown> | Record<string, unknown>[];
    input_payload?: Record<string, unknown> | null;
    alert_email?: string | null;
    alert_on_failure?: boolean;
  },
): Promise<AgentTrigger> => {
  const { data } = await http.patch<AgentTrigger>(
    `/workflows/${workflowId}/triggers/${triggerId}`,
    body,
  );
  return data;
};

export const deleteWorkflowTrigger = async (
  workflowId: string,
  triggerId: string,
): Promise<void> => {
  await http.delete(`/workflows/${workflowId}/triggers/${triggerId}`);
};

export const rotateWorkflowToken = async (
  workflowId: string,
  triggerId: string,
): Promise<RotateTokenResponse> => {
  const { data } = await http.post<RotateTokenResponse>(
    `/workflows/${workflowId}/triggers/${triggerId}/rotate-token`,
  );
  return data;
};

export const triggerWorkflowRun = async (
  workflowId: string,
  body: { input_payload: Record<string, unknown>; trigger_type?: string; run_by?: string },
): Promise<WorkflowRunResult> => {
  const { data } = await http.post<WorkflowRunResult>(`/workflows/${workflowId}/runs`, body);
  return data;
};

export const getWorkflowRunTree = async (
  workflowId: string,
  runId: string,
): Promise<WorkflowRunTree> => {
  const { data } = await http.get<WorkflowRunTree>(`/workflows/${workflowId}/runs/${runId}/tree`);
  return data;
};

export const listWorkflowRuns = async (
  workflowId: string,
  params?: { limit?: number; offset?: number; status?: string },
): Promise<AgentRunItem[]> => {
  const { data } = await http.get<AgentRunItem[]>(`/workflows/${workflowId}/runs`, { params });
  return data;
};

// ---------------------------------------------------------------------------
// Workflow Versions + Deployments (Slice 1b)
// ---------------------------------------------------------------------------
export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version_number: number;
  members: unknown[];
  edges: unknown[];
  orchestration: string;
  execution_shape: string;
  config: Record<string, unknown>;
  eval_passed: boolean;
  created_at: string;
  created_by: string | null;
}

export interface WorkflowDeployment {
  id: string;
  workflow_id: string;
  version_id: string;
  name: string | null;
  workflow_name: string | null;
  environment: string;
  status: string;
  replicas: number;
  ttl_hours: number | null;
  deployed_at: string;
  suspended_at: string | null;
  terminated_at: string | null;
  error_message: string | null;
  deployed_by: string | null;
  previous_version_id: string | null;
}

export const createWorkflowVersion = async (
  workflowId: string,
  body: { eval_passed?: boolean }
): Promise<WorkflowVersion> => {
  const { data } = await http.post<WorkflowVersion>(`/workflows/${workflowId}/versions`, body);
  return data;
};

export const listWorkflowVersions = async (workflowId: string): Promise<WorkflowVersion[]> => {
  const { data } = await http.get<WorkflowVersion[]>(`/workflows/${workflowId}/versions`);
  return data;
};

export const deleteWorkflowVersion = async (
  workflowId: string,
  versionId: string
): Promise<{ deleted_version_id: string; terminated_deployments: number }> => {
  const { data } = await http.delete(`/workflows/${workflowId}/versions/${versionId}`);
  return data;
};

export const patchWorkflowVersion = async (
  workflowId: string,
  versionId: string,
  body: { eval_passed?: boolean; notes?: string }
): Promise<WorkflowVersion> => {
  const { data } = await http.patch<WorkflowVersion>(
    `/workflows/${workflowId}/versions/${versionId}`,
    body
  );
  return data;
};

export const deployWorkflow = async (
  workflowId: string,
  body: { version_id: string; replicas?: number; environment?: string; name?: string; ttl_hours?: number }
): Promise<WorkflowDeployment> => {
  const { data } = await http.post<WorkflowDeployment>(`/workflows/${workflowId}/deploy`, body);
  return data;
};

export const listWorkflowDeployments = async (workflowId: string): Promise<WorkflowDeployment[]> => {
  const { data } = await http.get<WorkflowDeployment[]>(`/workflows/${workflowId}/deployments`);
  return data;
};

export const updateWorkflowDeployment = async (
  workflowId: string,
  deploymentId: string,
  action: DeploymentAction,
  versionId?: string
): Promise<WorkflowDeployment> => {
  const { data } = await http.patch<WorkflowDeployment>(
    `/workflows/${workflowId}/deployments/${deploymentId}`,
    { action, version_id: versionId }
  );
  return data;
};

export const getWorkflowDeploymentStats = async (
  workflowId: string,
  deploymentId: string
): Promise<AgentStats> => {
  const { data } = await http.get<AgentStats>(
    `/workflows/${workflowId}/deployments/${deploymentId}/stats`
  );
  return data;
};

export const listWorkflowDeploymentRuns = async (
  workflowId: string,
  deploymentId: string,
  params?: { status?: string; limit?: number; offset?: number }
): Promise<AgentRunItem[]> => {
  const { data } = await http.get<AgentRunItem[]>(
    `/workflows/${workflowId}/deployments/${deploymentId}/runs`,
    { params }
  );
  return data;
};

// ---------------------------------------------------------------------------
// Auth Configs (T156)
// ---------------------------------------------------------------------------
export interface AuthConfig {
  id: string;
  name: string;
  type: string;
  owner_team: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateAuthConfigPayload {
  name: string;
  type: 'api_key' | 'oauth2' | 'bearer' | 'mtls';
  credentials?: Record<string, string>;
  owner_team?: string;
}

export const listAuthConfigs = async (): Promise<Paginated<AuthConfig>> => {
  const { data } = await http.get<Paginated<AuthConfig>>("/auth-configs/");
  return data;
};

export const createAuthConfig = async (payload: CreateAuthConfigPayload): Promise<AuthConfig> => {
  const { data } = await http.post<AuthConfig>('/auth-configs/', payload);
  return data;
};

export const updateAuthConfig = async (
  id: string,
  payload: Partial<CreateAuthConfigPayload>
): Promise<AuthConfig> => {
  const { data } = await http.put<AuthConfig>(`/auth-configs/${id}`, payload);
  return data;
};

export const deleteAuthConfig = async (id: string): Promise<void> => {
  await http.delete(`/auth-configs/${id}`);
};

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------
export const listTools = async (
  limit = 100,
  offset = 0,
  params?: { team?: string }
): Promise<Paginated<RegistryTool>> => {
  const { data } = await http.get<Paginated<RegistryTool>>('/tools/', {
    params: { limit, offset, ...params },
  });
  return data;
};

export interface CreateToolPayload {
  name: string;
  display_name?: string;
  description?: string;
  type: 'http' | 'python';
  risk_level: 'low' | 'medium' | 'high';
  owner_team?: string;
  auth_config_id?: string | null;
  // HTTP-specific
  http_method?: string;
  http_url?: string;
  // Python-specific
  python_code?: string;
}

export const createTool = async (payload: CreateToolPayload): Promise<RegistryTool> => {
  const { data } = await http.post<RegistryTool>('/tools/', payload);
  return data;
};

export const updateTool = async (
  id: string,
  payload: Partial<CreateToolPayload & { display_name: string; description: string; owner_team: string }>
): Promise<RegistryTool> => {
  const { data } = await http.put<RegistryTool>(`/tools/${id}`, payload);
  return data;
};

export const deleteTool = async (id: string): Promise<void> => {
  await http.delete(`/tools/${id}`);
};

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------
export const listSkills = async (
  limit = 100,
  offset = 0,
  params?: { team?: string }
): Promise<Paginated<Skill>> => {
  const { data } = await http.get<Paginated<Skill>>('/skills/', {
    params: { limit, offset, ...params },
  });
  return data;
};

export const createSkill = async (payload: {
  name: string;
  team: string;
  description?: string;
  tool_ids: string[];
}): Promise<Skill> => {
  const { data } = await http.post<Skill>('/skills/', payload);
  return data;
};

export const updateSkill = async (
  id: string,
  payload: Partial<{ name: string; description: string; tool_ids: string[] }>
): Promise<Skill> => {
  const { data } = await http.put<Skill>(`/skills/${id}`, payload);
  return data;
};

export const deleteSkill = async (id: string): Promise<void> => {
  await http.delete(`/skills/${id}`);
};

// ---------------------------------------------------------------------------
// Publish Requests (Phase 9.2)
// ---------------------------------------------------------------------------
export interface PublishRequest {
  id: string;
  asset_id: string;
  asset_type: string;
  submitted_by: string;
  submitted_at: string;
  status: string;
  highest_risk_level: string;
  dependency_declaration: Record<string, unknown>;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  source_version_id: string | null;
  last_eval_score: number | null;
  last_eval_run_id: string | null;
  asset_name: string | null;
  asset_team: string | null;
}

export const publishAgent = async (
  name: string,
  opts?: { dependency_declaration?: Record<string, unknown>; version_id?: string }
): Promise<{ publish_request_id: string }> => {
  const { data } = await http.post<{ publish_request_id: string }>(
    `/agents/${name}/publish`,
    {
      dependency_declaration: opts?.dependency_declaration ?? {},
      ...(opts?.version_id ? { version_id: opts.version_id } : {}),
    }
  );
  return data;
};

export const listPublishRequests = async (params?: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<Paginated<PublishRequest>> => {
  const { data } = await http.get<Paginated<PublishRequest>>(
    "/admin/publish-requests",
    { params }
  );
  return data;
};

export const approvePublishRequest = async (
  id: string,
  body: { grantee_teams?: string[]; expires_at?: string } = {}
): Promise<{ approved: boolean; grants_created: number }> => {
  const { data } = await http.post<{ approved: boolean; grants_created: number }>(
    `/admin/publish-requests/${id}/approve`,
    body
  );
  return data;
};

export const rejectPublishRequest = async (
  id: string,
  notes?: string
): Promise<{ rejected: boolean }> => {
  const { data } = await http.post<{ rejected: boolean }>(
    `/admin/publish-requests/${id}/reject`,
    { notes: notes ?? "" }
  );
  return data;
};

// ---------------------------------------------------------------------------
// Asset Grants (Phase 9.2)
// ---------------------------------------------------------------------------
export interface AssetGrant {
  id: string;
  asset_id: string;
  asset_type: string;
  grantee_team: string;
  granted_by: string;
  granted_at: string;
  expires_at: string | null;
  revoked_at: string | null;
}

export const listGrants = async (params?: {
  asset_id?: string;
  grantee_team?: string;
  include_revoked?: boolean;
  limit?: number;
}): Promise<Paginated<AssetGrant>> => {
  const { data } = await http.get<Paginated<AssetGrant>>("/admin/grants", { params });
  return data;
};

export const createGrant = async (body: {
  asset_id: string;
  asset_type: string;
  grantee_team: string;
  expires_at?: string;
}): Promise<AssetGrant> => {
  const { data } = await http.post<AssetGrant>("/admin/grants", body);
  return data;
};

export const revokeGrant = async (id: string): Promise<void> => {
  await http.delete(`/admin/grants/${id}`);
};

export interface GrantAuditEntry {
  id: string;
  admin_id: string;
  action: string;
  asset_id: string;
  grantee_team: string;
  timestamp: string;
}

export const listGrantAudit = async (
  grantId: string,
  params?: { limit?: number; offset?: number }
): Promise<Paginated<GrantAuditEntry>> => {
  const { data } = await http.get<Paginated<GrantAuditEntry>>(
    `/admin/grants/${grantId}/audit`,
    { params }
  );
  return data;
};

export interface ApprovalAuthority {
  id: string;
  resource_type: string;
  resource_id: string;
  approver_user_id: string | null;
  approver_role: string | null;
  granted_by: string;
  granted_at: string;
  revoked_at: string | null;
}

export const listApprovalAuthority = async (params?: {
  resource_type?: string;
  resource_id?: string;
  include_revoked?: boolean;
  limit?: number;
}): Promise<Paginated<ApprovalAuthority>> => {
  const { data } = await http.get<Paginated<ApprovalAuthority>>(
    "/admin/approval-authority",
    { params }
  );
  return data;
};

export const createApprovalAuthority = async (body: {
  resource_type: string;
  resource_id: string;
  approver_user_id?: string;
  approver_role?: string;
}): Promise<ApprovalAuthority> => {
  const { data } = await http.post<ApprovalAuthority>("/admin/approval-authority", body);
  return data;
};

export const revokeApprovalAuthority = async (id: string): Promise<void> => {
  await http.delete(`/admin/approval-authority/${id}`);
};

// ---------------------------------------------------------------------------
// Current User
// ---------------------------------------------------------------------------
export interface MeResponse {
  sub: string;
  email: string | null;
  preferred_username: string | null;
  team: string | null;
  role: string | null;
}

export const getMe = async (): Promise<MeResponse> => {
  const { data } = await http.get<MeResponse>("/me");
  return data;
};

// ---------------------------------------------------------------------------
// Agent Triggers
// ---------------------------------------------------------------------------
export interface AgentTrigger {
  id: string;
  agent_id: string | null;
  workflow_id?: string | null;
  trigger_type: "schedule" | "webhook";
  cron_expression: string | null;
  timezone: string | null;
  enabled: boolean;
  filter_conditions: Record<string, unknown> | Record<string, unknown>[] | null;
  // Per-schedule job parameters fed to the agent as input on each fire (schedule triggers).
  input_payload?: Record<string, unknown> | null;
  alert_email: string | null;
  alert_on_failure: boolean;
  // Populated ONLY in the create response (shown once) for webhook triggers.
  token?: string | null;
  webhook_url?: string | null;
  // WS-2 T014 — the reviewer role a DAEMON (service-identity) trigger-run's approval
  // routes to. Only meaningful for daemon (scheduled) triggers; null = default scope.
  approver_role?: string | null;
  // WS-2 T014 — the human (Keycloak `sub`) who armed/created this trigger (audit).
  armed_by?: string | null;
  created_at: string;
  updated_at: string;
}

export const createTrigger = async (
  agentName: string,
  body: {
    trigger_type: "schedule" | "webhook";
    cron_expression?: string;
    timezone?: string;
    enabled?: boolean;
    filter_conditions?: Record<string, unknown> | Record<string, unknown>[];
    input_payload?: Record<string, unknown> | null;
    alert_email?: string | null;
    alert_on_failure?: boolean;
    // WS-2 T014 — daemon approver-role config, persisted on the trigger.
    approver_role?: string | null;
  }
): Promise<AgentTrigger> => {
  const { data } = await http.post<AgentTrigger>(
    `/agents/${agentName}/triggers`,
    body
  );
  return data;
};

export const listTriggers = async (
  agentName: string
): Promise<AgentTrigger[]> => {
  const { data } = await http.get<AgentTrigger[]>(
    `/agents/${agentName}/triggers`
  );
  return data;
};

export const updateTrigger = async (
  agentName: string,
  triggerId: string,
  body: {
    cron_expression?: string;
    timezone?: string;
    enabled?: boolean;
    filter_conditions?: Record<string, unknown> | Record<string, unknown>[];
    input_payload?: Record<string, unknown> | null;
    alert_email?: string | null;
    alert_on_failure?: boolean;
    // WS-2 T014 — daemon approver-role config, persisted on update.
    approver_role?: string | null;
  }
): Promise<AgentTrigger> => {
  const { data } = await http.patch<AgentTrigger>(
    `/agents/${agentName}/triggers/${triggerId}`,
    body
  );
  return data;
};

export const enableTrigger = (agentName: string, triggerId: string) =>
  updateTrigger(agentName, triggerId, { enabled: true });

export const disableTrigger = (agentName: string, triggerId: string) =>
  updateTrigger(agentName, triggerId, { enabled: false });

export const deleteTrigger = async (
  agentName: string,
  triggerId: string
): Promise<void> => {
  await http.delete(`/agents/${agentName}/triggers/${triggerId}`);
};

// ---------------------------------------------------------------------------
// Webhook token rotation + event log (Phase 9 — event gateway)
// ---------------------------------------------------------------------------
export interface RotateTokenResponse {
  trigger_id: string;
  token: string; // plaintext, shown once
  webhook_url: string;
}

export const rotateToken = async (
  agentName: string,
  triggerId: string
): Promise<RotateTokenResponse> => {
  const { data } = await http.post<RotateTokenResponse>(
    `/agents/${agentName}/triggers/${triggerId}/rotate-token`
  );
  return data;
};

export interface AgentEvent {
  id: string;
  trigger_id: string | null;
  agent_name: string;
  status: "matched" | "filtered" | "rejected";
  filter_reason: string | null;
  payload: Record<string, unknown> | null;
  run_id: string | null;
  source_ip: string | null;
  received_at: string;
}

export const listAgentEvents = async (
  agentName: string,
  params?: { trigger_id?: string; status?: string; limit?: number }
): Promise<AgentEvent[]> => {
  const { data } = await http.get<AgentEvent[]>(`/agents/${agentName}/events`, {
    params,
  });
  return data;
};

// ---------------------------------------------------------------------------
// Agent Stats
// ---------------------------------------------------------------------------
export interface AgentStats {
  run_count: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  error_rate: number;
  total_cost_usd: number;
}

export const getAgentStats = async (name: string): Promise<AgentStats> => {
  const { data } = await http.get<AgentStats>(`/agents/${name}/stats`);
  return data;
};

// ---------------------------------------------------------------------------
// Agent Health (mode-aware signals, Phase 8)
// ---------------------------------------------------------------------------
export type AgentHealthStatus = "healthy" | "degraded" | "failing";

export interface AgentHealth {
  agent_name: string;
  mode: "reactive" | "durable" | "scheduled" | "event-driven";
  health: AgentHealthStatus;
  // reactive
  p95_latency_ms: number | null;
  error_rate: number | null;
  runs_24h: number | null;
  cost_24h: number | null;
  // durable
  awaiting_approval_count: number | null;
  failed_24h: number | null;
  avg_duration_ms: number | null;
  // scheduled
  last_run_status: string | null;
  next_fire_at: string | null;
  missed_fires: number | null;
  // event-driven
  match_rate_24h: number | null;
  rejected_count_24h: number | null;
}

export const getAgentHealth = async (name: string): Promise<AgentHealth> => {
  const { data } = await http.get<AgentHealth>(`/agents/${name}/health`);
  return data;
};

// ---------------------------------------------------------------------------
// Agent Runs (production)
// ---------------------------------------------------------------------------
export interface AgentRunItem {
  id: string;
  agent_name: string;
  status: string;
  context: string;
  // Durable thread this run executes on; matches Approval.thread_id when a
  // high-risk tool parks the run (used to correlate the inline approval card).
  thread_id: string | null;
  trigger_type: string | null;
  run_by: string | null;
  team: string | null;
  input: string | null;
  output: string | null;
  error_message: string | null;
  latency_ms: number | null;
  cost_usd: number | null;
  started_at: string;
  completed_at: string | null;
  langfuse_trace_id: string | null;
  trace_url: string | null;
  production_deployment_id: string | null;
  sandbox_deployment_id: string | null;
  workflow_deployment_id: string | null;
}

export const listAgentRuns = async (params?: {
  agent_name?: string;
  trigger_type?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<AgentRunItem[]> => {
  const { data } = await http.get<AgentRunItem[]>("/agent-runs", { params });
  return data;
};

// ---------------------------------------------------------------------------
// Approvals Inbox
// ---------------------------------------------------------------------------
export interface ApprovalInboxItem {
  id: string;
  agent_name: string;
  team: string;
  step_name: string | null;
  tool_name: string;
  risk_level: string;
  tool_args: Record<string, unknown>;
  thread_context_snippet: string | null;
  sla_remaining_seconds: number;
  created_at: string;
  context: string;
  version: number;
  // The durable thread the gate parked at. For a workflow run this equals the
  // paused member run's `thread_id`, so the inline run panel can correlate an
  // approval to *this* run's step (WorkflowBuilderPage) rather than guessing by
  // agent_name. Always present (Approval.thread_id is NOT NULL).
  thread_id: string;
  // WS-2 T012 — reviewer scope + audit display derived server-side (approvals.py
  // `_derive_reviewer_audit`). `reviewer_scope` = the reviewer role a daemon
  // (service-identity) trigger-run's approval routes to (e.g. "agent:reviewer");
  // null for interactive/user-delegated approvals. `principal_display` reads like
  // "service:X on behalf of Y" / "workflow:X (service) on behalf of Y".
  reviewer_scope?: string | null;
  principal_display?: string | null;
}

// `context` selects the isolation the approval was raised in. Omit → backend
// defaults to `production` (the reviewer console). Pass `sandbox`/`playground`
// to list the self-service approvals surfaced inline in the run panel.
//
// `reviewerScope` narrows the inbox to approvals routed to one reviewer role
// (WS-2 T012/T013). The `GET /approvals/` endpoint does NOT accept a reviewer
// scope query param today, so this is applied CLIENT-SIDE over the returned rows
// (the list is already authority-scoped server-side). Omit → all scopes.
export const listPendingApprovals = async (
  team?: string,
  context?: "production" | "playground" | "sandbox",
  reviewerScope?: string,
): Promise<ApprovalInboxItem[]> => {
  const params: Record<string, string> = { status: "pending" };
  if (team) params.team = team;
  if (context) params.context = context;
  // Trailing slash = the canonical FastAPI path — avoids a 307 redirect that (behind
  // the TLS-terminating edge) downgrades to http:// and is blocked as mixed content.
  const { data } = await http.get<{ items: ApprovalInboxItem[] }>("/approvals/", { params });
  const items = data.items;
  return reviewerScope
    ? items.filter((it) => it.reviewer_scope === reviewerScope)
    : items;
};

export const decideApproval = async (
  approvalId: string,
  decision: "approved" | "rejected",
  version: number
): Promise<void> => {
  await http.patch(`/approvals/${approvalId}`, {
    decision,
    version,
    reviewer_id: "studio-user",
  });
};

export interface ChatApprovalStatus {
  run_id: string;
  approval_id: string | null;
  status: "none" | "pending" | "approved" | "rejected" | "timed_out";
  tool?: string;
  risk?: string;
  reasoning?: string | null;
  reviewer_id?: string | null;
  decided?: boolean;
}

// Requester-scoped poll: the person who started the chat watches their own
// approval status so the chat can auto-resume once a reviewer decides in the
// HITL console. No reviewer authority required (that gate is on PATCH /approvals).
export const getChatApprovalStatus = async (
  name: string,
  runId: string
): Promise<ChatApprovalStatus> => {
  const { data } = await http.get<ChatApprovalStatus>(
    `/agents/${name}/chat/${runId}/approval-status`
  );
  return data;
};

export interface SessionApproval {
  approval_id: string;
  run_id: string;
  status: "pending" | "approved" | "rejected" | "timed_out";
  tool: string;
  args: Record<string, unknown>;
  risk: string;
  reasoning: string | null;          // WHY — the LLM's stated reason (best-effort)
  requested_by: string | null;       // WHO — requester username
  requested_by_team: string | null;
  context: string;
  created_at: string | null;
  decided: boolean;
}

// Requester-scoped list of a conversation's approvals — feeds the sandbox
// self-approve panel. Usually one pending row today (graph interrupts at the
// first high-risk tool); list-shaped for future conversation history.
export const getSessionApprovals = async (
  name: string,
  sessionId: string
): Promise<SessionApproval[]> => {
  const { data } = await http.get<{ session_id: string; approvals: SessionApproval[] }>(
    `/agents/${name}/chat/session/${sessionId}/approvals`
  );
  return data.approvals;
};

// Sandbox self-approve: no reviewer authority (the developer approves their own
// test call). Reuses the context-agnostic playground decide endpoint.
export const decideSandboxApproval = async (
  approvalId: string,
  decision: "approved" | "denied"
): Promise<void> => {
  await http.post(`/playground/approvals/${approvalId}/decide`, { decision });
};

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------
export interface MemoryMessage {
  id: string;
  agent_name: string;
  thread_id: string;
  role: string;
  content: string;
  message_index: number;
  session_id: string | null;
  user_id: string | null;
  created_at: string;
}

export const listMemory = async (
  agentName: string,
  params?: { thread_id?: string; deployment_id?: string; limit?: number; offset?: number }
): Promise<MemoryMessage[]> => {
  const resp = await http.get(`/agents/${agentName}/memory`, { params });
  return resp.data;
};

export const deleteMemoryThread = async (
  agentName: string,
  threadId: string
): Promise<void> => {
  await http.delete(`/agents/${agentName}/memory/${threadId}`);
};

export const clearAgentMemory = async (agentName: string): Promise<void> => {
  await http.delete(`/agents/${agentName}/memory/clear`);
};
