import { http } from "./registryApi";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TraceSummary {
  id: string;
  agent_name: string;
  status: string;
  trigger_type: string | null;
  context: string;
  latency_ms: number | null;
  cost_usd: number | null;
  judge_score: number | null;
  started_at: string;
  completed_at: string | null;
  trace_id: string | null;
  trace_url: string | null;
  run_by: string | null;
}

export interface TracesListResponse {
  items: TraceSummary[];
  total: number;
  has_more: boolean;
}

export interface TimeseriesPoint {
  timestamp: string;
  p50: number | null;
  p95: number | null;
  total_usd: number | null;
  count: number;
}

export interface HistogramBucket {
  bucket: string;
  count: number;
}

export interface StatusCount {
  status: string;
  count: number;
}

export interface AgentBlockRate {
  agent_name: string;
  total_runs: number;
  blocked_runs: number;
}

export interface FeedbackSummary {
  up: number;
  down: number;
  total: number;
  ratio: number | null;
}

export interface DashboardData {
  latency_series: TimeseriesPoint[];
  score_histogram: HistogramBucket[];
  status_counts: StatusCount[];
  cost_series: TimeseriesPoint[];
  safety_blocks: AgentBlockRate[];
  feedback: FeedbackSummary;
  total_runs: number;
  total_cost_usd: number;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export interface TracesFilter {
  agent_name?: string;
  status?: string;
  trigger_type?: string;
  context?: string;
  from_date?: string;
  to_date?: string;
  limit?: number;
  offset?: number;
}

export async function listTraces(filters: TracesFilter = {}): Promise<TracesListResponse> {
  const params: Record<string, string | number> = {};
  if (filters.agent_name) params.agent_name = filters.agent_name;
  if (filters.status) params.status = filters.status;
  if (filters.trigger_type) params.trigger_type = filters.trigger_type;
  if (filters.context) params.context = filters.context;
  if (filters.from_date) params.from_date = filters.from_date;
  if (filters.to_date) params.to_date = filters.to_date;
  if (filters.limit) params.limit = filters.limit;
  if (filters.offset !== undefined) params.offset = filters.offset;
  const { data } = await http.get<TracesListResponse>("/observability/traces", { params });
  return data;
}

export async function getTraceDetail(traceId: string): Promise<{
  trace_id: string;
  trace_url: string | null;
  langfuse: Record<string, unknown>;
}> {
  const { data } = await http.get(`/observability/traces/${traceId}`);
  return data;
}

export async function getDashboard(params: {
  agent_name?: string;
  period?: string;
  environment?: "production" | "sandbox";
  from_date?: string;
  to_date?: string;
} = {}): Promise<DashboardData> {
  const { data } = await http.get<DashboardData>("/observability/dashboard", { params });
  return data;
}
