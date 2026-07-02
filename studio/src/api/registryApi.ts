import axios from "axios";
import { getKeycloak } from "../lib/keycloak";

const http = axios.create({ baseURL: "/api/v1" });

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
  workflow_id: string | null;
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
  status?: string
): Promise<Paginated<Agent>> => {
  const { data } = await http.get<Paginated<Agent>>("/agents/", {
    params: { limit, offset, ...(status !== undefined ? { status } : {}) },
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
  llm_provider_id?: string;
}): Promise<Agent> => {
  const { data } = await http.post<Agent>("/agents/", body);
  return data;
};

export const updateAgent = async (
  name: string,
  body: { description?: string; status?: string }
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
    `/agents/${agentName}/versions/`
  );
  return data;
};

export const createVersion = async (
  agentName: string,
  body: { image_tag?: string; eval_passed?: boolean; notes?: string }
): Promise<AgentVersion> => {
  const { data } = await http.post<AgentVersion>(
    `/agents/${agentName}/versions/`,
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
    `/agents/${agentName}/deployments/`
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
// Workflows
// ---------------------------------------------------------------------------
export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version_number: number;
  definition: { nodes: unknown[]; edges: unknown[] };
  change_summary: string | null;
  created_at: string;
}

export interface Workflow {
  id: string;
  name: string;
  team: string;
  description: string | null;
  status: string;
  current_version_number?: number | null;
  current_definition?: WorkflowVersion | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowDefinition {
  nodes: unknown[];
  edges: unknown[];
}

export const createWorkflow = async (body: {
  name: string;
  team: string;
  description?: string;
  definition: WorkflowDefinition;
}): Promise<Workflow> => {
  const { data } = await http.post<Workflow>("/workflows/", body);
  return data;
};

export const updateWorkflow = async (
  id: string,
  body: { definition: WorkflowDefinition; change_summary?: string }
): Promise<Workflow> => {
  const { data } = await http.put<Workflow>(`/workflows/${id}`, body);
  return data;
};

export const deployWorkflow = async (
  id: string,
  body?: { replicas?: number }
): Promise<unknown> => {
  const { data } = await http.post(`/workflows/${id}/deploy`, body ?? {});
  return data;
};

export const listWorkflows = async (params?: { team?: string }): Promise<Workflow[]> => {
  const { data } = await http.get<Workflow[]>('/workflows/', { params });
  return data;
};

export const getWorkflow = async (id: string): Promise<Workflow> => {
  const { data } = await http.get<Workflow>(`/workflows/${id}`);
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
