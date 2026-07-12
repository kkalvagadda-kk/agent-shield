import { http } from "./registryApi";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface CatalogArtifact {
  id: string;
  name: string;
  type: string;
  description: string | null;
  source_id: string | null;
  team: string;
  created_at: string;
  updated_at: string;
  latest_version: string | null;
  deployment_count: number;
}

export interface CatalogVersion {
  id: string;
  artifact_id: string;
  version_label: string;
  config_snapshot: Record<string, unknown>;
  source_version_id: string | null;
  source_version_number: number | null;
  promoted_at: string;
  promoted_by: string | null;
  notes: string | null;
}

export interface CatalogDeployment {
  id: string;
  artifact_id: string;
  version_id: string;
  version_label: string | null;
  status: string;
  namespace: string | null;
  deployed_at: string | null;
  suspended_at: string | null;
  updated_at: string;
}

export interface MemberTopologyEntry {
  agent_name: string;
  agent_id: string;
  agent_version_id: string | null;
  role: string | null;
  position: number | null;
  has_production_deployment: boolean;
}

export interface CatalogDetail {
  artifact: CatalogArtifact;
  versions: CatalogVersion[];
  deployments: CatalogDeployment[];
  granted_teams: string[];
  member_topology: MemberTopologyEntry[];
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------
export async function listCatalog(params?: {
  team?: string;
  type?: string;
}): Promise<CatalogArtifact[]> {
  const { data } = await http.get("/catalog", { params });
  return data;
}

export async function getCatalogDetail(
  artifactId: string
): Promise<CatalogDetail> {
  const { data } = await http.get(`/catalog/${artifactId}`);
  return data;
}

export async function deployVersion(
  artifactId: string,
  versionId: string
): Promise<CatalogDeployment> {
  const { data } = await http.post(`/catalog/${artifactId}/deploy`, {
    version_id: versionId,
  });
  return data;
}

export async function updateDeployment(
  artifactId: string,
  deploymentId: string,
  action: "upgrade" | "suspend" | "resume" | "terminate",
  versionId?: string
): Promise<CatalogDeployment> {
  const { data } = await http.patch(
    `/catalog/${artifactId}/deployments/${deploymentId}`,
    { action, version_id: versionId }
  );
  return data;
}

export interface CatalogRun {
  id: string;
  agent_name: string;
  status: string;
  context: string;
  trigger_type: string | null;
  run_by: string | null;
  user_id: string | null;
  input: string | null;
  output: string | null;
  error_message: string | null;
  latency_ms: number | null;
  judge_score: number | null;
  cost_usd: number | null;
  started_at: string;
  completed_at: string | null;
  trace_url: string | null;
  langfuse_trace_id: string | null;
  production_deployment_id: string | null;
}

export interface FleetDeployment {
  id: string;
  artifact_id: string;
  artifact_name: string;
  artifact_type: string;
  version_id: string;
  version_label: string | null;
  status: string;
  namespace: string | null;
  deployed_at: string | null;
  suspended_at: string | null;
  updated_at: string | null;
}

export async function listFleetDeployments(
  status?: string
): Promise<FleetDeployment[]> {
  const { data } = await http.get("/catalog/deployments", {
    params: status ? { status } : undefined,
  });
  return data;
}

export interface CatalogStats {
  run_count: number;
  error_rate: number;
  p50_latency_ms: number | null;
  total_cost_usd: number;
}

export async function getCatalogStats(
  artifactId: string
): Promise<CatalogStats> {
  const { data } = await http.get(`/catalog/${artifactId}/stats`);
  return data;
}

export async function listCatalogRuns(
  artifactId: string,
  limit = 50
): Promise<CatalogRun[]> {
  const { data } = await http.get(`/catalog/${artifactId}/runs`, {
    params: { limit },
  });
  return data;
}
