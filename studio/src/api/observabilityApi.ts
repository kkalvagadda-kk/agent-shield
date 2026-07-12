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

export interface ToolCallStat {
  tool_name: string;
  count: number;
  avg_latency_ms: number | null;
}

// Provider-neutral trace shape returned by the observability backend read-adapter.
// Studio consumes THIS, never a backend-specific (Langfuse) raw shape.
export interface NormalizedSpan {
  id: string;
  name: string;
  type: string;
  parent_id?: string | null;
  start_time: string | null;
  end_time: string | null;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown> | null;
  status_message?: string | null;
  level?: string | null;
  model?: string | null;
  cost_usd?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
}

export interface NormalizedScore {
  name: string;
  value: number | null;
  comment: string | null;
}

export interface NormalizedTrace {
  trace_id: string;
  name: string | null;
  user: string | null;
  started_at: string | null;
  tags: string[];
  total_cost: number | null;
  warning: string | null;
  spans: NormalizedSpan[];
  scores: NormalizedScore[];
}

export interface TraceDetail {
  trace_id: string;
  trace_url: string | null;
  trace: NormalizedTrace | null;
}

export interface CostByModel {
  model: string;
  cost_usd: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface CostByAgent {
  agent_name: string;
  cost_usd: number;
  runs: number;
}

export interface ExpensiveRun {
  id: string;
  agent_name: string;
  cost_usd: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  started_at: string;
  trace_id: string | null;
}

export interface DashboardData {
  latency_series: TimeseriesPoint[];
  score_histogram: HistogramBucket[];
  status_counts: StatusCount[];
  cost_series: TimeseriesPoint[];
  safety_blocks: AgentBlockRate[];
  feedback: FeedbackSummary;
  tool_calls: ToolCallStat[];
  total_runs: number;
  total_cost_usd: number;
  avg_cost_per_run: number | null;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  spend_by_model: CostByModel[];
}

export interface CostConsoleData {
  environment: string;
  total_cost_usd: number;
  total_runs: number;
  runs_with_cost: number;
  avg_cost_per_run: number | null;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  projected_monthly_usd: number | null;
  daily_series: TimeseriesPoint[];
  by_model: CostByModel[];
  by_agent: CostByAgent[];
  top_runs: ExpensiveRun[];
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

export async function getTraceDetail(traceId: string): Promise<TraceDetail> {
  const { data } = await http.get<TraceDetail>(`/observability/traces/${traceId}`);
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

export async function getCosts(params: {
  period?: string;
  environment?: "production" | "sandbox";
  from_date?: string;
  to_date?: string;
} = {}): Promise<CostConsoleData> {
  const { data } = await http.get<CostConsoleData>("/observability/costs", { params });
  return data;
}
