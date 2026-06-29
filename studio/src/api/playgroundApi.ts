import axios from "axios";

const http = axios.create({ baseURL: "/api/v1" });

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface PlaygroundRun {
  id: string;
  user_id: string;
  agent_name: string;
  agent_version_id: string | null;
  context: string;
  sandbox: boolean;
  input_message: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface PlaygroundDataset {
  id: string;
  owner_user_id: string;
  name: string;
  items: DatasetItem[];
  created_at: string;
}

export interface DatasetItem {
  input: string;
  expected_output?: string;
}

export interface EvalRun {
  id: string;
  user_id: string;
  agent_name: string;
  agent_version_id: string | null;
  dataset_id: string;
  status: string;
  total_items: number | null;
  passed_count: number | null;
  failed_count: number | null;
  overall_score: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface EvalRunResult {
  id: string;
  eval_run_id: string;
  dataset_item_idx: number;
  input_message: string | null;
  response: string | null;
  judge_score: number | null;
  judge_reasoning: string | null;
  passed: boolean | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Playground Runs
// ---------------------------------------------------------------------------
export async function startPlaygroundRun(body: {
  agent_name: string;
  input_message: string;
  agent_version_id?: string;
}): Promise<{ run_id: string; stream_url: string }> {
  const { data } = await http.post<{ run_id: string; stream_url: string }>(
    "/playground/runs",
    body
  );
  return data;
}

export async function listPlaygroundRuns(): Promise<PlaygroundRun[]> {
  const { data } = await http.get<PlaygroundRun[]>("/playground/runs");
  return data;
}

export function streamPlaygroundRun(runId: string): EventSource {
  return new EventSource(`/api/v1/playground/runs/${runId}/stream`);
}

// ---------------------------------------------------------------------------
// Playground Approvals
// ---------------------------------------------------------------------------
export async function listPlaygroundApprovals(statusFilter?: string): Promise<unknown[]> {
  const params: Record<string, string> = {};
  if (statusFilter) params.status = statusFilter;
  const { data } = await http.get<unknown[]>("/playground/approvals", { params });
  return data;
}

export async function decidePlaygroundApproval(
  approvalId: string,
  decision: "approved" | "denied"
): Promise<void> {
  await http.post(`/playground/approvals/${approvalId}/decide`, { decision });
}

// ---------------------------------------------------------------------------
// Datasets
// ---------------------------------------------------------------------------
export async function listDatasets(): Promise<PlaygroundDataset[]> {
  const { data } = await http.get<PlaygroundDataset[]>("/playground/datasets");
  return data;
}

export async function createDataset(body: {
  name: string;
  items: DatasetItem[];
}): Promise<PlaygroundDataset> {
  const { data } = await http.post<PlaygroundDataset>("/playground/datasets", body);
  return data;
}

export async function deleteDataset(id: string): Promise<void> {
  await http.delete(`/playground/datasets/${id}`);
}

// ---------------------------------------------------------------------------
// Eval Runs
// ---------------------------------------------------------------------------
export async function createEvalRun(body: {
  agent_name: string;
  dataset_id: string;
  agent_version_id?: string;
}): Promise<EvalRun> {
  const { data } = await http.post<EvalRun>("/playground/eval-runs", body);
  return data;
}

export async function listEvalRuns(): Promise<EvalRun[]> {
  const { data } = await http.get<EvalRun[]>("/playground/eval-runs");
  return data;
}

export async function getEvalRun(id: string): Promise<EvalRun> {
  const { data } = await http.get<EvalRun>(`/playground/eval-runs/${id}`);
  return data;
}

export async function getEvalRunResults(id: string): Promise<EvalRunResult[]> {
  const { data } = await http.get<EvalRunResult[]>(`/playground/eval-runs/${id}/results`);
  return data;
}
