import { useQuery } from "@tanstack/react-query";
import { DollarSign, TrendingUp, Cpu, Bot, Coins } from "lucide-react";
import { useState } from "react";
import { getCosts, CostConsoleData } from "../api/observabilityApi";

const PERIOD_OPTIONS = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
];

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${n}`;
}

function fmtUsd(n: number | null | undefined, digits = 4): string {
  if (n == null) return "—";
  return `$${n.toFixed(digits)}`;
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-slate-800 mt-1">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function CostConsolePage() {
  const [environment, setEnvironment] = useState<"production" | "sandbox">("production");
  const [period, setPeriod] = useState("30d");

  const { data, isLoading } = useQuery({
    queryKey: ["cost-console", environment, period],
    queryFn: () => getCosts({ environment, period }),
    staleTime: 30_000,
  });

  const d = data as CostConsoleData | undefined;
  const maxModel = d?.by_model.length ? Math.max(...d.by_model.map((m) => m.cost_usd)) : 0;
  const maxAgent = d?.by_agent.length ? Math.max(...d.by_agent.map((a) => a.cost_usd)) : 0;
  const maxDay = d?.daily_series.length
    ? Math.max(...d.daily_series.map((p) => p.total_usd ?? 0))
    : 0;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <DollarSign size={20} className="text-green-600" />
          <h1 className="text-xl font-semibold text-slate-800">Cost Console</h1>
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
          {/* Environment toggle — sandbox spend never mixes with production */}
          <div className="flex rounded border border-slate-200 overflow-hidden text-sm">
            {(["production", "sandbox"] as const).map((env) => (
              <button
                key={env}
                onClick={() => setEnvironment(env)}
                className={`px-3 py-1.5 capitalize ${
                  environment === env
                    ? "bg-slate-800 text-white"
                    : "bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                {env}
              </button>
            ))}
          </div>
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
          Loading cost data…
        </div>
      ) : !d ? (
        <div className="text-center py-16 text-slate-400 text-sm">No data available.</div>
      ) : (
        <>
          {/* Headline stats */}
          <div className="grid grid-cols-4 gap-4">
            <StatCard
              label="Total Spend"
              value={fmtUsd(d.total_cost_usd)}
              sub={`${d.runs_with_cost}/${d.total_runs} runs costed`}
            />
            <StatCard label="Avg / Run" value={fmtUsd(d.avg_cost_per_run)} />
            <StatCard
              label="Tokens"
              value={fmtTokens(d.total_prompt_tokens + d.total_completion_tokens)}
              sub={`${fmtTokens(d.total_prompt_tokens)}↑ ${fmtTokens(d.total_completion_tokens)}↓`}
            />
            <StatCard
              label="Projected / Month"
              value={fmtUsd(d.projected_monthly_usd, 2)}
              sub="extrapolated from window"
            />
          </div>

          {/* Daily spend trend */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
              <TrendingUp size={14} /> Daily Spend
            </h2>
            {d.daily_series.length === 0 ? (
              <p className="text-sm text-slate-400">No spend in this period.</p>
            ) : (
              <div className="flex items-end gap-1 h-40">
                {d.daily_series.map((p, i) => (
                  <div key={i} className="flex-1 flex flex-col items-center justify-end group">
                    <div
                      className="w-full bg-green-400 rounded-t hover:bg-green-500 transition-colors"
                      style={{ height: `${Math.max(2, ((p.total_usd ?? 0) / Math.max(0.000001, maxDay)) * 100)}%` }}
                      title={`${fmtUsd(p.total_usd)} · ${p.count} runs`}
                    />
                    <span className="text-[9px] text-slate-400 mt-1 rotate-0">
                      {new Date(p.timestamp).toLocaleDateString(undefined, { month: "numeric", day: "numeric" })}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* By model + by agent */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
                <Cpu size={14} /> Spend by Model
              </h2>
              {d.by_model.length === 0 ? (
                <p className="text-sm text-slate-400">No model cost recorded.</p>
              ) : (
                <div className="space-y-2">
                  {d.by_model.map((m) => (
                    <div key={m.model} className="text-xs">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="font-mono text-slate-600 truncate" title={m.model}>{m.model}</span>
                        <span className="text-slate-700 shrink-0 ml-2">{fmtUsd(m.cost_usd)}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                          <div
                            className="h-full bg-green-400 rounded"
                            style={{ width: `${Math.min(100, (m.cost_usd / Math.max(0.000001, maxModel)) * 100)}%` }}
                          />
                        </div>
                        <span className="text-slate-400 w-24 text-right shrink-0">
                          {m.calls}× · {fmtTokens(m.prompt_tokens + m.completion_tokens)} tok
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="bg-white border border-slate-200 rounded-lg p-4">
              <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
                <Bot size={14} /> Spend by Agent
              </h2>
              {d.by_agent.length === 0 ? (
                <p className="text-sm text-slate-400">No agent cost recorded.</p>
              ) : (
                <div className="space-y-2">
                  {d.by_agent.map((a) => (
                    <div key={a.agent_name} className="text-xs">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="text-slate-600 truncate" title={a.agent_name}>{a.agent_name}</span>
                        <span className="text-slate-700 shrink-0 ml-2">{fmtUsd(a.cost_usd)}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                          <div
                            className="h-full bg-blue-400 rounded"
                            style={{ width: `${Math.min(100, (a.cost_usd / Math.max(0.000001, maxAgent)) * 100)}%` }}
                          />
                        </div>
                        <span className="text-slate-400 w-12 text-right shrink-0">{a.runs} runs</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Most expensive runs */}
          <div className="bg-white border border-slate-200 rounded-lg p-4">
            <h2 className="text-sm font-medium text-slate-700 mb-3 flex items-center gap-1.5">
              <Coins size={14} /> Most Expensive Runs
            </h2>
            {d.top_runs.length === 0 ? (
              <p className="text-sm text-slate-400">No costed runs yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-slate-400 border-b border-slate-100 text-left">
                      <th className="py-1.5 pr-3 font-medium">Agent</th>
                      <th className="py-1.5 pr-3 font-medium text-right">Cost</th>
                      <th className="py-1.5 pr-3 font-medium text-right">Tokens</th>
                      <th className="py-1.5 pr-3 font-medium">When</th>
                      <th className="py-1.5 font-medium">Trace</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.top_runs.map((r) => (
                      <tr key={r.id} className="border-b border-slate-50 hover:bg-slate-50">
                        <td className="py-1.5 pr-3 text-slate-700">{r.agent_name}</td>
                        <td className="py-1.5 pr-3 text-right font-medium text-slate-800">{fmtUsd(r.cost_usd)}</td>
                        <td className="py-1.5 pr-3 text-right text-slate-500">
                          {fmtTokens((r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0))}
                        </td>
                        <td className="py-1.5 pr-3 text-slate-400">
                          {new Date(r.started_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                        </td>
                        <td className="py-1.5">
                          {r.trace_id ? (
                            <a
                              href={`/observability/traces?trace=${r.trace_id}`}
                              className="text-blue-600 hover:text-blue-700 font-mono"
                            >
                              {r.trace_id.slice(0, 8)}…
                            </a>
                          ) : (
                            <span className="text-slate-300">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
