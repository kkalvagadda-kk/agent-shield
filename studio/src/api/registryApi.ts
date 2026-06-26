import axios from "axios";

const http = axios.create({ baseURL: "/api/v1" });

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
  created_at: string;
  updated_at: string;
  created_by: string | null;
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

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------
export const listAgents = async (
  limit = 100,
  offset = 0
): Promise<Paginated<Agent>> => {
  const { data } = await http.get<Paginated<Agent>>("/agents/", {
    params: { limit, offset },
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
export interface Workflow {
  id: string;
  name: string;
  team: string;
  description: string | null;
  status: string;
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
