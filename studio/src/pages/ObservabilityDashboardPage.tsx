import { useQuery } from "@tanstack/react-query";
import { BarChart3, TrendingUp, ThumbsUp, ThumbsDown, Wrench, DollarSign, ArrowRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { getDashboard, DashboardData, FeedbackSummary } from "../api/observabilityApi";
import { listAgents } from "../api/registryApi";

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

const PERIOD_OPTIONS = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

function MetricCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-slate-800 mt-1">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function FeedbackRow({ label, fb }: { label: string; fb?: FeedbackSummary }) {
  const total = fb?.total ?? 0;
  const ratio = fb?.ratio ?? 0;
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="font-medium text-slate-600">{label}</span>
        {total === 0 ? (
          <span className="text-slate-300">no feedback yet</span>
        ) : (
          <span className="text-slate-500">
            {Math.round(ratio * 100)}% positive · {total} rated
          </span>
        )}
      </div>
      <div className="flex h-3 w-full overflow-hidden rounded bg-slate-100">
        {total > 0 && (
          <>
            <div className="h-full bg-emerald-400" style={{ width: `${ratio * 100}%` }} />
            <div className="h-full flex-1 bg-rose-300" />
          </>
        )}
      </div>
      {total > 0 && (
        <div className="flex items-center justify-between text-[11px] text-slate-500 mt-0.5">
          <span className="flex items-center gap-1 text-emerald-600"><ThumbsUp size={11} /> {fb?.up ?? 0}</span>
          <span className="flex items-center gap-1 text-rose-500">{fb?.down ?? 0} <ThumbsDown size={11} /></span>
        </div>
      )}
    </div>
  );
}

export default function ObservabilityDashboardPage({
  environment = "production",
}: {
  environment?: "production" | "sandbox";
}) {
  const [period, setPeriod] = useState("7d");
  const [agentName, setAgentName] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["observability-dashboard", environment, period, agentName],
    queryFn: () => getDashboard({ environment, period, agent_name: agentName || undefined }),
    staleTime: 30_000,
  });

  const { data: agentsData } = useQuery({
    queryKey: ["agents-for-filter"],
    queryFn: () => listAgents(200, 0, "active"),
    staleTime: 60_000,
  });

  const agents = agentsData?.items ?? [];
  const d = data as DashboardData | undefined;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3
            size={20}
            className={environment === "production" ? "text-emerald-500" : "text-amber-500"}
          />
          <h1 className="text-xl font-semibold text-slate-800">
            {environment === "production" ? "Production" : "Sandbox"} Dashboard
          </h1>
          <span
            className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${
              environment === "production"
                ? "bg-emerald-50 text-emerald-700"
                : "bg-amber-50 text-amber-700"
            }`}
          >
            {environment}
          </span>
        </div>
        <div className="flex gap-2">
          <select
            value={agentName}
            onChange={(e) => setAgentName(e.target.value)}
            className="text-sm border border-slate-200 rounded px-2 py-1.5"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.name} value={a.name}>{a.name}</option>
            ))}
          </select>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            className="text-sm border border-slate-200 rounded px-2 py-1.5"
          >
            {PERIOD_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-slate-400 text-sm">
          Loading dashboard…
        </div>
      ) : !d ? (
        <div className="text-center py-16 text-slate-400 text-sm">
          No data available.
        </div>
      ) : (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-4 gap-4">
            <MetricCard label="Total Runs" value={d.total_runs.toLocaleString()} />
            <MetricCard label="Total Cost" value={`$${d.total_cost_usd.toFixed(4)}`} />
            <MetricCard
              label="Avg Latency (P50)"
              value={
                d.latency_series.length > 0
                  ? `${((d.latency_series.reduce((s, p) => s + (p.p50 ?? 0), 0) / d.latency_series.length) / 1000).toFixed(1)}s`
                  : "—"
              }
            />
            <MetricCard
              label="Satisfaction"
              value={
                d.feedback?.ratio != null
                  ? `${Math.round(d.feedback.ratio * 100)}%`
                  : "—"
              }
              sub={
                d.feedback?.total
                  ? `${d.feedback.up}👍 / ${d.feedback.down}👎`
                  : "no feedback yet"
              }
            />
          </div>

          {/* Cost — headline cost signal next to quality; deep dive in Cost console */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-slate-700 flex items-center gap-1.5">
                <DollarSign size={14} /> LLM Cost
              </h2>
              <Link
                to="/observability/costs"
                className="text-xs text-blue-600 hover:text-blue-700 flex items-center gap-1"
              >
                Cost console <ArrowRight size={12} />
              </Link>
            </div>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <p className="text-xs text-slate-500 uppercase tracking-wide">Avg / Run</p>
                <p className="text-lg font-semibold text-slate-800 mt-0.5">
                  {d.avg_cost_per_run != null ? `$${d.avg_cost_per_run.toFixed(4)}` : "—"}
                </p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase tracking-wide">Prompt Tokens</p>
                <p className="text-lg font-semibold text-slate-800 mt-0.5">{fmtTokens(d.total_prompt_tokens)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500 uppercase tracking-wide">Completion Tokens</p>
                <p className="text-lg font-semibold text-slate-800 mt-0.5">{fmtTokens(d.total_completion_tokens)}</p>
              </div>
            </div>
            <p className="text-xs font-medium text-slate-500 mb-2">Spend by model</p>
            {!d.spend_by_model || d.spend_by_model.length === 0 ? (
              <p className="text-sm text-slate-400">No LLM cost recorded in this period.</p>
            ) : (
              <div className="space-y-1">
                {d.spend_by_model.map((m) => {
                  const max = Math.max(...d.spend_by_model.map((x) => x.cost_usd));
                  return (
                    <div key={m.model} className="flex items-center gap-2 text-xs">
                      <span className="text-slate-600 w-48 truncate font-mono" title={m.model}>
                        {m.model}
                      </span>
                      <div className="flex-1 bg-slate-100 rounded h-4 overflow-hidden">
                        <div
                          className="h-full bg-green-400 rounded"
                          style={{ width: `${Math.min(100, (m.cost_usd / Math.max(0.000001, max)) * 100)}%` }}
                        />
                      </div>
                      <span className="text-slate-700 w-20 text-right">${m.cost_usd.toFixed(4)}</span>
                      <span className="text-slate-400 w-12 text-right">{m.calls}×</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Latency chart (simple text-based for now; Recharts added in M2 chart task) */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
              <TrendingUp size={14} /> Latency (P50 / P95)
            </h2>
            {d.latency_series.length === 0 ? (
              <p className="text-sm text-slate-400">No latency data in this period.</p>
            ) : (
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {d.latency_series.map((p, i) => (
                  <div key={i} className="flex items-center gap-3 text-xs">
                    <span className="text-slate-400 w-32 shrink-0">
                      {new Date(p.timestamp).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit" })}
                    </span>
                    <div className="flex-1 flex items-center gap-2">
                      <div
                        className="h-3 bg-blue-200 rounded"
                        style={{ width: `${Math.min(100, ((p.p50 ?? 0) / 10000) * 100)}%` }}
                      />
                      <span className="text-slate-600">
                        P50: {p.p50 != null ? `${(p.p50 / 1000).toFixed(1)}s` : "—"}
                      </span>
                      <span className="text-slate-400">
                        P95: {p.p95 != null ? `${(p.p95 / 1000).toFixed(1)}s` : "—"}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Status & Score */}
          <div className="grid grid-cols-2 gap-4">
            {/* Status distribution */}
            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h2 className="text-sm font-medium text-slate-700 mb-3">Status Distribution</h2>
              {d.status_counts.length === 0 ? (
                <p className="text-sm text-slate-400">No data.</p>
              ) : (
                <div className="space-y-2">
                  {d.status_counts.map((s) => (
                    <div key={s.status} className="flex items-center justify-between text-sm">
                      <span className="capitalize text-slate-700">{s.status}</span>
                      <span className="font-medium text-slate-800">{s.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Score histogram */}
            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h2 className="text-sm font-medium text-slate-700 mb-3">Judge Score Distribution</h2>
              {d.score_histogram.length === 0 ? (
                <p className="text-sm text-slate-400">No scores recorded yet.</p>
              ) : (
                <div className="space-y-1">
                  {d.score_histogram.map((b) => (
                    <div key={b.bucket} className="flex items-center gap-2 text-xs">
                      <span className="text-slate-500 w-16">{b.bucket}</span>
                      <div className="flex-1 bg-slate-100 rounded h-4 overflow-hidden">
                        <div
                          className="h-full bg-blue-400 rounded"
                          style={{ width: `${Math.min(100, (b.count / Math.max(1, ...d.score_histogram.map(x => x.count))) * 100)}%` }}
                        />
                      </div>
                      <span className="text-slate-600 w-8 text-right">{b.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* User feedback — production vs sandbox (production is the signal that matters) */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
              <ThumbsUp size={14} /> User Feedback
            </h2>
            <FeedbackRow
              label={environment === "production" ? "Production" : "Sandbox"}
              fb={d.feedback}
            />
          </div>

          {/* Tool calls — frequency + avg latency (from OTEL TOOL spans) */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
              <Wrench size={14} /> Tool Calls
            </h2>
            {!d.tool_calls || d.tool_calls.length === 0 ? (
              <p className="text-sm text-slate-400">
                No tool calls recorded in this period.
              </p>
            ) : (
              <div className="space-y-1">
                {d.tool_calls.map((t) => {
                  const max = Math.max(...d.tool_calls.map((x) => x.count));
                  return (
                    <div key={t.tool_name} className="flex items-center gap-2 text-xs">
                      <span className="text-slate-600 w-40 truncate font-mono" title={t.tool_name}>
                        {t.tool_name}
                      </span>
                      <div className="flex-1 bg-slate-100 rounded h-4 overflow-hidden">
                        <div
                          className="h-full bg-indigo-400 rounded"
                          style={{ width: `${Math.min(100, (t.count / Math.max(1, max)) * 100)}%` }}
                        />
                      </div>
                      <span className="text-slate-700 w-10 text-right">{t.count}×</span>
                      <span className="text-slate-400 w-16 text-right">
                        {t.avg_latency_ms != null ? `${(t.avg_latency_ms / 1000).toFixed(2)}s` : "—"}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Safety blocks */}
          {d.safety_blocks.length > 0 && (
            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h2 className="text-sm font-medium text-slate-700 mb-3">Safety Blocks by Agent</h2>
              <div className="space-y-2">
                {d.safety_blocks.map((a) => (
                  <div key={a.agent_name} className="flex items-center justify-between text-sm">
                    <span className="text-slate-700">{a.agent_name}</span>
                    <span className="text-orange-600 font-medium">
                      {a.blocked_runs}/{a.total_runs} blocked
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
