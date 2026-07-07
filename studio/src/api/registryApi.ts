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
}

export interface AgentVersion {
  id: string;
  agent_id: string;
  version_number: number;
  image_tag: string | null;
  agent_graph_id: string | null;
  tools: { name: string; risk: string; description?: string }[];
  eval_passed: boolean;
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
}

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
  // HTTP tool fields (top-level in ToolResponse)
  http_method?: string;
  http_url?: string;
  // Python tool fields
  python_code?: string;
  config: Record<string, unknown>;
}

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

// ---------------------------------------------------------------------------
// Deployments
// ---------------------------------------------------------------------------
export const deployAgent = async (
  agentName: string,
  body: { version_id: string; replicas?: number; environment?: string }
): Promise<Deployment> => {
  const { data } = await http.post<Deployment>(
    `/agents/${agentName}/deploy`,
    body
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

export interface AgentChatStart {
  run_id: string;
  session_id: string;
  stream_url: string;
  agent_name: string;
  deployment_id: string;
}

export const startAgentChat = async (
  name: string,
  body: { message: string; session_id?: string }
): Promise<AgentChatStart> => {
  const { data } = await http.post<AgentChatStart>(`/agents/${name}/chat`, body);
  return data;
};

export const listAllDeployments = async (
  status?: string,
  limit = 100
): Promise<Paginated<Deployment>> => {
  const { data } = await http.get<Paginated<Deployment>>("/deployments/", {
    params: { limit, ...(status ? { status } : {}) },
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
  memory_enabled: boolean;
  status: 'draft' | 'published' | 'archived';
  publish_status: string;
  member_count: number;
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
// Auth Configs (T156)
// ---------------------------------------------------------------------------
export interface AuthConfig {
  id: string;
  name: string;
  type: string;
}

export const listAuthConfigs = async (): Promise<Paginated<AuthConfig>> => {
  const { data } = await http.get<Paginated<AuthConfig>>("/auth-configs/");
  return data;
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
}

export const publishAgent = async (
  name: string,
  dependency_declaration?: Record<string, unknown>
): Promise<{ publish_request_id: string }> => {
  const { data } = await http.post<{ publish_request_id: string }>(
    `/agents/${name}/publish`,
    { dependency_declaration: dependency_declaration ?? {} }
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
}

export const listPendingApprovals = async (
  team?: string
): Promise<ApprovalInboxItem[]> => {
  const params: Record<string, string> = { status: "pending" };
  if (team) params.team = team;
  const { data } = await http.get<{ items: ApprovalInboxItem[] }>("/approvals", { params });
  return data.items;
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
  params?: { thread_id?: string; limit?: number; offset?: number }
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
